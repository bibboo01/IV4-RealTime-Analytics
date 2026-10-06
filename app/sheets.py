"""
Live summary dashboard in Google Sheets.

The agent keeps one spreadsheet ("IV4 Dashboard", inside the "IV4 Data Agent"
Drive folder) up to date from the hourly summary tables. Only summary numbers
leave the machine - never images or per-inspection rows - and every refresh is
one small batched API call (~a few hundred cells), so it costs the Mini PC
next to nothing and never blocks ingestion (own thread, like the uploader).

Tabs (fixed size, so charts never need re-pointing)
    Today      per sensor: total, NG, NG %, yield, missing, cycle time, run hours
    Hourly     last 48 hours, all sensors  (+ 2 charts)
    Daily      last 30 days, all sensors   (+ 1 chart)
    Tools      NG per tool today
    Latest NG  last 20 NG inspections
    Status     agent heartbeat, backlog, disk, errors, last update

Same Google sign-in as the Drive uploader (scope drive.file: the agent only
sees spreadsheets it created itself). Needs the "Google Sheets API" enabled
once in the Google Cloud project.

    run sheets            publish once now and print the link
    IV4_SHEETS_ENABLED=true   keep it updating while the agent runs
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import text

from app.config import Settings
from app.database.repository import DatabaseRepository
from app.metrics import summarize, tool_summary
from app.monitor import _read_health, _sum
from app.upload.google_drive import DriveConfigError, GoogleDriveUploader, build_drive_service, build_sheets_service

log = logging.getLogger(__name__)

HOURS = 48
DAYS = 30
LATEST_NG = 20
TODAY_ROWS = 10
TOOL_ROWS = 31
STATUS_ROWS = 19

SHEET_MIME = "application/vnd.google-apps.spreadsheet"
TABS = ["Today", "Hourly", "Daily", "Tools", "Latest NG", "Status"]


# ------------------------------------------------------------------
# tables (pure data, no Google)
# ------------------------------------------------------------------

def _v(x):
    return "" if x is None else x


def _pad(rows: list[list], n: int, width: int) -> list[list]:
    rows = [r + [""] * (width - len(r)) for r in rows[:n]]
    return rows + [[""] * width for _ in range(n - len(rows))]


def _upload_line(s: Settings, health: dict) -> str:
    if not s.upload_enabled:
        return "OFF (IV4_UPLOAD_ENABLED=false)"
    what = "ALL images" if s.upload_statuses is None else "only " + "+".join(sorted(s.upload_statuses))
    err = (health.get("upload") or {}).get("last_upload_error")
    return f"ON - {what}" + (f" - ERROR: {err[:120]}" if err else "")


def _upload_counts(health: dict) -> str:
    up = health.get("upload") or {}
    if not up.get("enabled"):
        return "-"
    return f"{up.get('uploaded', 0):,} sent / {up.get('pending', 0):,} waiting"


def _retention_line(s: Settings) -> str:
    def d(n: int) -> str:
        return f"{n} days" if n > 0 else "forever"
    return f"OK {d(s.retention_ok_days)}, NG {d(s.retention_ng_days)}"


def build_tables(repo: DatabaseRepository, s: Settings, now: datetime | None = None,
                 health: dict | None = None) -> dict[str, list[list]]:
    now = now or datetime.now()
    today = now.strftime("%Y-%m-%d")
    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    health = health if health is not None else (_read_health(s) or {})

    # --- Today ---------------------------------------------------------
    rows = summarize(repo, today, tomorrow, by="day", per_sensor=True)
    if len(rows) > 1:
        rows.append(_sum(rows))
    t_today = [["Sensor", "Total", "NG", "NG %", "Yield %", "Missing", "Avg ms", "Max ms", "Run h"]]
    for r in rows:
        t_today.append([r.sensor_id or "-", r.total, r.fail_count, _v(r.ng_pct), _v(r.yield_pct), _v(r.missing),
                        _v(r.avg_time_ms), _v(r.max_time_ms), r.active_hours])

    # --- Hourly (fixed 48 rows, oldest first) ------------------------------
    first = now - timedelta(hours=HOURS - 1)
    by_hour = {r.period: r for r in summarize(repo, first.strftime("%Y-%m-%d %H"), tomorrow, by="hour")}
    t_hour = [["Hour", "Total", "NG", "NG %", "Yield %", "Missing"]]
    for i in range(HOURS):
        h = first + timedelta(hours=i)
        r = by_hour.get(h.strftime("%Y-%m-%d %H"))
        label = h.strftime("%m-%d %H:00")
        t_hour.append([label, r.total, r.fail_count, _v(r.ng_pct), _v(r.yield_pct), _v(r.missing)]
                      if r else [label, 0, 0, "", "", ""])

    # --- Daily (fixed 30 rows) ----------------------------------------------
    first_d = (now - timedelta(days=DAYS - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    by_day = {r.period: r for r in summarize(repo, first_d.strftime("%Y-%m-%d"), tomorrow, by="day")}
    t_day = [["Day", "Total", "NG", "NG %", "Yield %", "Missing", "Run h"]]
    for i in range(DAYS):
        d = first_d + timedelta(days=i)
        r = by_day.get(d.strftime("%Y-%m-%d"))
        label = d.strftime("%Y-%m-%d")
        t_day.append([label, r.total, r.fail_count, _v(r.ng_pct), _v(r.yield_pct), _v(r.missing), r.active_hours]
                     if r else [label, 0, 0, "", "", "", 0])

    # --- Tools ---------------------------------------------------------------
    t_tools = [["Sensor", "Tool", "Name", "Count", "NG", "NG %", "Avg value", "Lowest value", "Lowest OK value"]]
    for t in sorted(tool_summary(repo, today, tomorrow, per_sensor=True),
                    key=lambda t: (-t.ng_count, t.sensor_id or "", t.tool_no)):
        t_tools.append([t.sensor_id or "-", t.tool_no, _v(t.tool_name), t.count, t.ng_count, _v(t.ng_pct),
                        _v(t.avg_value), _v(t.min_value), _v(t.min_ok_value)])

    # --- Latest NG -----------------------------------------------------------
    with repo.engine.connect() as c:
        ng = c.execute(text(
            "SELECT timestamp, camera_id, inspection_id, analysis_reason FROM inspection "
            "WHERE analysis_status = 'FAIL' ORDER BY id DESC LIMIT :n"), {"n": LATEST_NG}).fetchall()
    t_ng = [["Time (sensor clock)", "Sensor", "Inspection", "Reason"]]
    for ts, sensor, iid, reason in ng:
        why = "; ".join(p for p in (reason or "").split("; ") if not p.startswith("Inspection result is"))
        t_ng.append([(ts or "")[:19], sensor or "-", iid, why])

    # --- Status --------------------------------------------------------------
    t_status = [["Item", "Value"],
                ["Dashboard updated", now.strftime("%Y-%m-%d %H:%M:%S") + " (sensor clock / this PC)"],
                ["Agent heartbeat (UTC)", health.get("heartbeat_at", "-")],
                ["Program version", health.get("version", "-")],
                ["Agent started (UTC)", health.get("started_at", "-")],
                ["Processed since start", health.get("processed", "-")],
                ["PASS since start", health.get("pass", "-")],
                ["FAIL since start", health.get("fail", "-")],
                ["UNKNOWN since start", health.get("unknown", "-")],
                ["Files waiting", health.get("incoming_files", "-")],
                ["Errors since start", health.get("errors", "-")],
                ["Last error", (health.get("last_error") or "-")[:200]],
                ["Disk free (GB)", health.get("disk_free_gb", "-")],
                ["Google Drive upload", _upload_line(s, health)],
                ["Uploaded / waiting", _upload_counts(health)],
                ["Keep images on this PC", _retention_line(s)],
                ["Disk guard", f"deletes oldest OK images when free space < {s.min_free_gb:g} GB"
                               if s.disk_prune_ok else f"warning only below {s.min_free_gb:g} GB"],
                ["Rule", "Missing must be 0: sensor counted, file never arrived (check FTP)"]]

    return {
        "Today": _pad(t_today, TODAY_ROWS, 9),
        "Hourly": _pad(t_hour, HOURS + 1, 6),
        "Daily": _pad(t_day, DAYS + 1, 7),
        "Tools": _pad(t_tools, TOOL_ROWS, 9),
        "Latest NG": _pad(t_ng, LATEST_NG + 1, 4),
        "Status": _pad(t_status, STATUS_ROWS, 2),
    }


# ------------------------------------------------------------------
# Google Sheets
# ------------------------------------------------------------------

def _col(n: int) -> str:
    return "ABCDEFGHIJKLMNOPQRSTUVWXYZ"[n - 1]


def _status(exc: Exception) -> int | None:
    resp = getattr(exc, "resp", None)
    return getattr(resp, "status", None)


def _explain(exc: Exception) -> Exception:
    """Turn the two common setup mistakes into messages a person can act on."""
    content = getattr(exc, "content", b"")
    msg = str(exc) + " " + (content.decode("utf-8", "replace") if isinstance(content, bytes) else str(content))
    if "SERVICE_DISABLED" in msg or "has not been used in project" in msg or "is disabled" in msg:
        return DriveConfigError(
            "Google Sheets API is not enabled. In Google Cloud Console (same project as the Drive sign-in): "
            "APIs & Services -> Library -> 'Google Sheets API' -> Enable. Then run again.")
    if "insufficient authentication scopes" in msg or "ACCESS_TOKEN_SCOPE_INSUFFICIENT" in msg:
        return DriveConfigError("Google sign-in lacks permission for Sheets. Re-run: run gdrive-auth")
    return exc


def _chart(sheet_id: int, title: str, kind: str, y_col: int, y_title: str, rows: int, anchor_col: int) -> dict:
    def rng(col: int) -> dict:
        return {"sources": [{"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": rows,
                             "startColumnIndex": col, "endColumnIndex": col + 1}]}
    return {"addChart": {"chart": {
        "spec": {
            "title": title,
            "basicChart": {
                "chartType": kind, "legendPosition": "NO_LEGEND", "headerCount": 1,
                "axis": [{"position": "BOTTOM_AXIS"}, {"position": "LEFT_AXIS", "title": y_title}],
                "domains": [{"domain": {"sourceRange": rng(0)}}],
                "series": [{"series": {"sourceRange": rng(y_col)}, "targetAxis": "LEFT_AXIS"}],
            },
        },
        "position": {"overlayPosition": {
            "anchorCell": {"sheetId": sheet_id, "rowIndex": 1, "columnIndex": anchor_col},
            "widthPixels": 620, "heightPixels": 300}},
    }}}


class SheetsPublisher:

    def __init__(self, settings: Settings, sheets=None, drive=None):
        self.settings = settings
        self._sheets = sheets
        self._drive = drive
        self.spreadsheet_id: str | None = None
        self._state_loaded = False
        self.url: str | None = None

    # -- services ----------------------------------------------------
    @property
    def sheets(self):
        if self._sheets is None:
            self._sheets = build_sheets_service(self.settings)
        return self._sheets

    @property
    def drive(self):
        if self._drive is None:
            self._drive = build_drive_service(self.settings)
        return self._drive

    def _state_file(self) -> Path:
        base = self.settings.gdrive_token.parent if self.settings.gdrive_token else self.settings.log_dir
        return base / "sheets_state.json"

    def _load_state(self) -> dict:
        try:
            return json.loads(self._state_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_state(self, state: dict) -> None:
        f = self._state_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(state), encoding="utf-8")

    # -- spreadsheet lifecycle ---------------------------------------------
    def _create(self) -> str:
        root = GoogleDriveUploader(self.settings, self.drive).root_id()
        sid = self.drive.files().create(
            body={"name": self.settings.sheets_title, "mimeType": SHEET_MIME, "parents": [root]},
            fields="id", supportsAllDrives=True).execute(num_retries=3)["id"]
        log.info("[SHEETS] created spreadsheet '%s' id=%s", self.settings.sheets_title, sid)
        return sid

    def _titles(self, sid: str) -> dict[str, int]:
        meta = self.sheets.spreadsheets().get(
            spreadsheetId=sid, fields="sheets(properties(sheetId,title))").execute(num_retries=3)
        return {sh["properties"]["title"]: sh["properties"]["sheetId"] for sh in meta.get("sheets", [])}

    def _setup(self, sid: str, state: dict) -> None:
        """Make sure all tabs exist; add charts once."""
        ids = self._titles(sid)
        reqs: list[dict] = []
        missing = [t for t in TABS if t not in ids]
        if missing and "Sheet1" in ids and "Today" in missing:        # fresh sheet: reuse the default tab
            reqs.append({"updateSheetProperties": {
                "properties": {"sheetId": ids["Sheet1"], "title": "Today"}, "fields": "title"}})
            missing.remove("Today")
        for t in missing:
            reqs.append({"addSheet": {"properties": {"title": t}}})
        if reqs:
            self.sheets.spreadsheets().batchUpdate(spreadsheetId=sid, body={"requests": reqs}).execute(num_retries=3)
            ids = self._titles(sid)
        if not state.get("charts"):
            self.sheets.spreadsheets().batchUpdate(spreadsheetId=sid, body={"requests": [
                _chart(ids["Hourly"], f"Inspections per hour (last {HOURS} h)", "COLUMN", 1, "Inspections",
                       HOURS + 1, 7),
                _chart(ids["Hourly"], "NG % per hour", "LINE", 3, "NG %", HOURS + 1, 7 + 12),
                _chart(ids["Daily"], f"NG % per day (last {DAYS} days)", "LINE", 3, "NG %", DAYS + 1, 8),
            ]}).execute(num_retries=3)
            state["charts"] = True
            self._save_state(state)

    def ensure(self) -> str:
        state = self._load_state()
        sid = self.spreadsheet_id or state.get("spreadsheet_id")
        if sid:
            try:
                self._setup(sid, state)
            except Exception as exc:  # noqa: BLE001
                if _status(exc) in (403, 404):       # deleted / no longer ours -> start a new one
                    log.warning("[SHEETS] spreadsheet %s not usable (%s) - creating a new one", sid, exc)
                    sid, state = None, {}
                else:
                    raise
        if not sid:
            sid = self._create()
            state = {"spreadsheet_id": sid}
            self._save_state(state)
            self._setup(sid, state)
        self.spreadsheet_id = sid
        self.url = f"https://docs.google.com/spreadsheets/d/{sid}/edit"
        return sid

    # -- publish ------------------------------------------------------------
    def publish(self, tables: dict[str, list[list]]) -> str:
        try:
            sid = self.ensure()
            data = [{"range": f"'{tab}'!A1:{_col(len(rows[0]))}{len(rows)}", "values": rows}
                    for tab, rows in tables.items()]
            self.sheets.spreadsheets().values().batchUpdate(
                spreadsheetId=sid, body={"valueInputOption": "RAW", "data": data}).execute(num_retries=3)
        except DriveConfigError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _explain(exc) from exc
        return self.url or ""


def publish_once(s: Settings, repo: DatabaseRepository, publisher: SheetsPublisher | None = None) -> str:
    publisher = publisher or SheetsPublisher(s)
    return publisher.publish(build_tables(repo, s))


class SheetsWorker(threading.Thread):
    """Background thread: refresh the dashboard every sheets_interval seconds."""

    def __init__(self, settings: Settings, stop_event: threading.Event, publisher: SheetsPublisher | None = None):
        super().__init__(name="sheets-worker", daemon=True)
        self.settings = settings
        self.stop_event = stop_event
        self._publisher = publisher
        self.stats = {"updates": 0, "last_update_at": None, "last_error": None, "url": None}

    def run(self) -> None:
        log.info("[SHEETS] worker started interval=%.0fs", self.settings.sheets_interval)
        repo = DatabaseRepository(self.settings.database_path)
        try:
            while not self.stop_event.is_set():
                try:
                    if self._publisher is None:
                        self._publisher = SheetsPublisher(self.settings)
                    url = publish_once(self.settings, repo, self._publisher)
                    if self.stats["updates"] == 0:
                        log.info("[SHEETS] dashboard: %s", url)
                    self.stats.update(updates=self.stats["updates"] + 1, url=url, last_error=None,
                                      last_update_at=datetime.now(timezone.utc).isoformat())
                except DriveConfigError as exc:
                    if self.stats["last_error"] != str(exc):
                        log.error("[SHEETS] configuration problem: %s", exc)
                    self.stats["last_error"] = str(exc)
                    self._publisher = None          # rebuild after the human fixes it
                except Exception as exc:  # noqa: BLE001 - network down etc.; try again next round
                    log.warning("[SHEETS] update failed: %s", exc)
                    self.stats["last_error"] = str(exc)[:500]
                self.stop_event.wait(self.settings.sheets_interval)
        finally:
            repo.dispose()
