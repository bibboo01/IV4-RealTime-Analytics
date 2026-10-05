"""Google Sheets dashboard: table content, publishing flow (fake API) and request shape (real API schema)."""
import itertools
import json
import threading
from datetime import datetime

import pytest

from app import sheets
from app.config import load_settings
from app.sheets import SheetsPublisher, SheetsWorker, build_tables, publish_once
from app.upload.google_drive import DriveConfigError
from tests.test_cli import _seed_today


class _Req:
    def __init__(self, fn):
        self.fn = fn

    def execute(self, num_retries=0):
        return self.fn()


class FakeApi:
    """Drive files().create + Sheets spreadsheets().get/batchUpdate/values().batchUpdate."""

    def __init__(self):
        self.ids = itertools.count(1)
        self.sheets = {}                 # spreadsheetId -> {title: sheetId}
        self.written = {}                # spreadsheetId -> {range: rows}
        self.batch_updates = []          # (spreadsheetId, requests)
        self.value_writes = []
        self.fail = None                 # exception to raise on next values write
        self.created = []

    # Drive
    def files(self):
        return self

    def create(self, body, fields, **kw):
        def do():
            assert body["mimeType"] == sheets.SHEET_MIME
            sid = f"sheet{next(self.ids)}"
            self.sheets[sid] = {"Sheet1": 0}
            self.created.append(body)
            return {"id": sid}
        return _Req(do)

    def list(self, q, **kw):                       # GoogleDriveUploader.root_id() folder lookup
        return _Req(lambda: {"files": [{"id": "root1", "name": "IV4 Data Agent", "size": "0"}]})

    # Sheets
    def spreadsheets(self):
        return self

    def get(self, spreadsheetId, fields=None):
        def do():
            if spreadsheetId not in self.sheets:
                raise _http_error(404)
            return {"sheets": [{"properties": {"sheetId": i, "title": t}}
                               for t, i in self.sheets[spreadsheetId].items()]}
        return _Req(do)

    def batchUpdate(self, spreadsheetId, body):
        def do():
            self.batch_updates.append((spreadsheetId, body["requests"]))
            tabs = self.sheets[spreadsheetId]
            for r in body["requests"]:
                if "addSheet" in r:
                    tabs[r["addSheet"]["properties"]["title"]] = 100 + len(tabs)
                if "updateSheetProperties" in r:
                    p = r["updateSheetProperties"]["properties"]
                    old = next(t for t, i in tabs.items() if i == p["sheetId"])
                    tabs[p["title"]] = tabs.pop(old)
            return {}
        return _Req(do)

    def values(self):
        return _Values(self)


class _Values:
    def __init__(self, api):
        self.api = api

    def batchUpdate(self, spreadsheetId, body):
        def do():
            if self.api.fail:
                exc, self.api.fail = self.api.fail, None
                raise exc
            assert body["valueInputOption"] == "RAW"
            self.api.value_writes.append(body["data"])
            self.api.written[spreadsheetId] = {d["range"]: d["values"] for d in body["data"]}
            return {}
        return _Req(do)


def _http_error(status, content=b"{}"):
    class Resp(dict):
        pass
    r = Resp(status=status)
    r.status = status
    r.reason = "x"
    from googleapiclient.errors import HttpError
    return HttpError(r, content)


@pytest.fixture
def gsettings(tmp_path):
    s = load_settings(base_dir=tmp_path, overrides={"IV4_SHEETS_ENABLED": "true"})
    s.ensure_dirs()
    return s


@pytest.fixture
def seeded(gsettings):
    from app.database.repository import DatabaseRepository
    now = datetime.now()
    _seed_today(gsettings, now)
    repo = DatabaseRepository(gsettings.database_path)
    yield repo, now
    repo.dispose()


def test_tables_have_fixed_shape_and_right_numbers(seeded, gsettings):
    repo, now = seeded
    t = build_tables(repo, gsettings, now=now, health={"processed": 1990, "errors": 0})
    assert list(t) == sheets.TABS
    assert len(t["Hourly"]) == sheets.HOURS + 1 and len(t["Daily"]) == sheets.DAYS + 1
    assert len(t["Today"]) == sheets.TODAY_ROWS and len(t["Latest NG"]) == sheets.LATEST_NG + 1
    today = {r[0]: r for r in t["Today"] if r[0]}
    assert today["IV4-01"][1:3] == [1000, 20] and today["IV4-02"][1:3] == [990, 50]
    assert today["ALL"][1:3] == [1990, 70] and today["ALL"][3] == 3.518         # NG %
    assert today["IV4-02"][5] == 10                                              # missing
    hourly = t["Hourly"]
    assert hourly[-1][0] == now.strftime("%m-%d %H:00") and hourly[-1][1] == 1990    # newest hour last
    assert all(r[1] == 0 for r in hourly[1:-1])                                  # gaps filled with 0
    assert t["Daily"][-1][1] == 1990
    assert t["Tools"][1][:5] == ["IV4-02", 2, "AI Differentiate", 990, 50]
    assert t["Latest NG"][1][2] == "00042_x" and t["Latest NG"][1][3] == "Tool02:AI Differentiate NG (value=12)"
    assert ["Processed since start", 1990] in t["Status"]
    json.dumps(t)                                                                # JSON-serialisable for the API
    # every table is rectangular
    for rows in t.values():
        assert len({len(r) for r in rows}) == 1


def test_tables_when_no_data_yet(gsettings):
    from app.database.repository import DatabaseRepository
    repo = DatabaseRepository(gsettings.database_path)
    t = build_tables(repo, gsettings, health={})
    assert t["Today"][1] == [""] * 9 and all(r[1] == 0 for r in t["Hourly"][1:])
    repo.dispose()


def test_first_publish_creates_sheet_tabs_charts_and_values(seeded, gsettings):
    repo, _ = seeded
    api = FakeApi()
    pub = SheetsPublisher(gsettings, sheets=api, drive=api)
    url = publish_once(gsettings, repo, pub)
    sid = "sheet1"
    assert url == f"https://docs.google.com/spreadsheets/d/{sid}/edit"
    assert api.created[0]["name"] == "IV4 Dashboard" and api.created[0]["parents"] == ["root1"]
    assert set(api.sheets[sid]) == set(sheets.TABS)                      # Sheet1 renamed + 5 added
    charts = [r for _, reqs in api.batch_updates for r in reqs if "addChart" in r]
    assert len(charts) == 3
    ranges = api.written[sid]
    assert "'Hourly'!A1:F49" in ranges and "'Today'!A1:I10" in ranges and "'Status'!A1:B14" in ranges
    assert json.loads(gsettings.gdrive_token.parent.joinpath("sheets_state.json").read_text()) == {
        "spreadsheet_id": sid, "charts": True}


def test_second_publish_reuses_sheet_and_adds_no_charts(seeded, gsettings):
    repo, _ = seeded
    api = FakeApi()
    publish_once(gsettings, repo, SheetsPublisher(gsettings, sheets=api, drive=api))
    api2 = api
    publish_once(gsettings, repo, SheetsPublisher(gsettings, sheets=api2, drive=api2))   # e.g. agent restarted
    assert len(api.created) == 1
    assert sum(1 for _, reqs in api.batch_updates for r in reqs if "addChart" in r) == 3
    assert len(api.value_writes) == 2                                                    # one call per refresh


def test_deleted_spreadsheet_is_recreated(seeded, gsettings):
    repo, _ = seeded
    api = FakeApi()
    pub = SheetsPublisher(gsettings, sheets=api, drive=api)
    publish_once(gsettings, repo, pub)
    api.sheets.clear()                                                   # user deleted it
    publish_once(gsettings, repo, SheetsPublisher(gsettings, sheets=api, drive=api))
    assert len(api.created) == 2 and "sheet2" in api.written


def test_api_not_enabled_gives_actionable_message(seeded, gsettings):
    repo, _ = seeded
    api = FakeApi()
    api.fail = _http_error(403, (b'{"error":{"code":403,"message":"Google Sheets API has not been used in project 1 before or it is '
        b'disabled.","status":"PERMISSION_DENIED","details":[{"reason":"SERVICE_DISABLED"}]}}'))
    with pytest.raises(DriveConfigError, match="Google Sheets API is not enabled"):
        publish_once(gsettings, repo, SheetsPublisher(gsettings, sheets=api, drive=api))


def test_worker_updates_and_survives_errors(seeded, gsettings):
    repo, _ = seeded
    api = FakeApi()
    gsettings.sheets_interval = 0.05
    stop = threading.Event()
    api.fail = ConnectionError("network down")
    w = SheetsWorker(gsettings, stop, publisher=SheetsPublisher(gsettings, sheets=api, drive=api))
    w.start()
    for _ in range(100):
        if w.stats["updates"] >= 2:
            break
        stop.wait(0.05)
    stop.set()
    w.join(timeout=5)
    assert w.stats["updates"] >= 2 and w.stats["last_error"] is None     # failed once, recovered by itself
    assert w.stats["url"].startswith("https://docs.google.com/spreadsheets/d/")


# ------------------------------------------------------------------
# Request shape against Google's real API description (bundled with google-api-python-client)
# ------------------------------------------------------------------

def _check(schemas, name, obj, path="body"):
    """Every key we send must exist in the schema Google publishes."""
    sch = schemas[name]
    props = sch.get("properties", {})
    for k, v in obj.items():
        assert k in props, f"{path}.{k} is not a field of {name}"
        _check_value(schemas, props[k], v, f"{path}.{k}")


def _check_value(schemas, spec, v, path):
    if "$ref" in spec:
        _check(schemas, spec["$ref"], v, path)
    elif spec.get("type") == "array":
        for i, item in enumerate(v):
            _check_value(schemas, spec["items"], item, f"{path}[{i}]")
    elif spec.get("type") == "object" and "additionalProperties" in spec:
        pass


def test_requests_match_real_sheets_api_schema(seeded, gsettings):
    from googleapiclient.discovery_cache import get_static_doc
    doc = json.loads(get_static_doc("sheets", "v4"))
    schemas = doc["schemas"]
    repo, _ = seeded
    api = FakeApi()
    publish_once(gsettings, repo, SheetsPublisher(gsettings, sheets=api, drive=api))
    assert api.batch_updates
    for _, reqs in api.batch_updates:
        _check(schemas, "BatchUpdateSpreadsheetRequest", {"requests": reqs})
    _check(schemas, "BatchUpdateValuesRequest",
           {"valueInputOption": "RAW", "data": api.value_writes[0]})
    # enum values we use exist
    chart = schemas["BasicChartSpec"]["properties"]
    assert {"COLUMN", "LINE"} <= set(chart["chartType"]["enum"])
    assert {"NO_LEGEND"} <= set(chart["legendPosition"]["enum"])


def test_real_client_accepts_our_calls(seeded, gsettings):
    """Build the real discovery client against a mock transport: parameter names/URLs are validated."""
    from googleapiclient.discovery import build
    from googleapiclient.http import HttpMockSequence

    ok = ({"status": "200"}, json.dumps({"sheets": [{"properties": {"sheetId": 0, "title": "Today"}}]}))
    http = HttpMockSequence([ok] * 20)
    svc = build("sheets", "v4", http=http, static_discovery=True)
    SheetsPublisher(gsettings, sheets=svc, drive=_NoDrive())._titles("abc")
    svc.spreadsheets().values().batchUpdate(
        spreadsheetId="abc", body={"valueInputOption": "RAW", "data": []}).execute()
    svc.spreadsheets().batchUpdate(spreadsheetId="abc", body={"requests": []}).execute()
    uris = [r[0] for r in http.request_sequence]
    assert any(u.endswith("/v4/spreadsheets/abc/values:batchUpdate?alt=json") for u in uris)
    assert any("/v4/spreadsheets/abc:batchUpdate" in u for u in uris)
    assert any("fields=sheets%28properties%28sheetId%2Ctitle%29%29" in u for u in uris)


class _NoDrive:
    pass


def test_schema_check_catches_a_wrong_field_name():
    """Guards the guard: a typo in a request must fail the schema test."""
    from googleapiclient.discovery_cache import get_static_doc
    schemas = json.loads(get_static_doc("sheets", "v4"))["schemas"]
    bad = {"requests": [{"addChart": {"chart": {"spec": {"basicChart": {"chartTyp": "COLUMN"}}}}}]}
    with pytest.raises(AssertionError, match="chartTyp"):
        _check(schemas, "BatchUpdateSpreadsheetRequest", bad)


def test_run_sheets_command_without_signin_explains(gsettings, capsys):
    from app import cli
    assert cli.cmd_sheets([], gsettings) == 1
    out = capsys.readouterr().out
    assert "Cannot publish" in out and "run gdrive-auth" in out
