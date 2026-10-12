"""The Grafana dashboard queries must run on the real schema (macros replaced by a fixed range)."""
import json
import sqlite3
import time
from pathlib import Path

from tests.test_report_xlsx import _seed

DASH = Path(__file__).parent.parent / "deploy" / "grafana" / "iv4-dashboard.json"


def test_dashboard_json_is_in_sync_with_generator():
    import importlib.util
    spec = importlib.util.spec_from_file_location("bd", DASH.with_name("build_dashboard.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert json.loads(DASH.read_text(encoding="utf-8")) == mod.build()


def test_every_panel_query_runs_and_returns_rows(settings):
    _seed(settings)
    con = sqlite3.connect(settings.database_path)
    con.execute("UPDATE hourly_stats SET trigger_max = trigger_max + 25 WHERE rowid = (SELECT MAX(rowid) FROM hourly_stats)")
    now = int(time.time())
    for p in json.loads(DASH.read_text(encoding="utf-8"))["panels"]:
        sql = p["targets"][0]["queryText"].replace("$__from", str((now - 10 * 86400) * 1000)) \
                                          .replace("$__to", str(now * 1000))
        rows = con.execute(sql).fetchall()
        assert rows, p["title"]
        if p["title"].startswith("Missing"):
            assert rows[0][3] == 25
