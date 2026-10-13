"""run export-table: one flat file per day, tool columns, safe re-runs."""
import gzip
import csv
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from app import export_table
from app.database.repository import DatabaseRepository

DAY = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")


def _seed(settings, n=30):
    repo = DatabaseRepository(settings.database_path)
    with repo.engine.begin() as c:
        for i in range(n):
            ts = f"{DAY} {(i % 24):02d}:{i:02d}:00"
            tools = [{"no": 1, "name": "AI", "status": "NG" if i % 10 == 0 else "OK", "value": 40 if i % 10 == 0 else 99},
                     {"no": 2, "name": "AI", "status": "OK", "value": 98.5}]
            c.execute(text(
                "INSERT INTO inspection (uid, inspection_id, timestamp, camera_id, program_no, trigger_no, analysis_status,"
                " analysis_reason, score, inspection_time_ms, image_file, folder, app_version, config_hash, tools_json,"
                " created_at, updated_at) VALUES (:u, :i, :ts, 'IV4-01', 0, :t, :st, :r, 98.0, 35, 'a.jpg', 'f', '1.0', 'abc',"
                " :tj, :c, :c)"),
                {"u": f"u{i}", "i": str(i), "ts": ts, "t": i, "st": "FAIL" if i % 10 == 0 else "PASS",
                 "r": "Tool01 NG" if i % 10 == 0 else None, "tj": json.dumps(tools), "c": datetime.utcnow()})
        for h in range(24):
            c.execute(text(
                "INSERT INTO hourly_tool_stats (hour, program_no, sensor_id, tool_no, tool_name, count, ng_count, value_sum, value_count)"
                " VALUES (:h, 0, 'IV4-01', :t, 'AI', 1, 0, 1, 1)"), {"h": f"{DAY} {h:02d}", "t": 1})
            c.execute(text(
                "INSERT INTO hourly_tool_stats (hour, program_no, sensor_id, tool_no, tool_name, count, ng_count, value_sum, value_count)"
                " VALUES (:h, 0, 'IV4-01', :t, 'AI', 1, 0, 1, 1)"), {"h": f"{DAY} {h:02d}", "t": 2})
    repo.dispose()


def _read_csv(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_csv_day_file_has_tool_columns_and_is_not_overwritten(settings, tmp_path, capsys):
    _seed(settings)
    out = tmp_path / "ex"
    assert export_table.run(["--day", DAY, "--format", "csv", "--out", str(out)], settings) == 0
    rows = _read_csv(out / f"inspections_{DAY}.csv.gz")
    assert len(rows) == 30 and rows[0]["tool1_status"] in ("NG", "OK") and "tool2_value" in rows[0]
    assert {r["status"] for r in rows} == {"PASS", "FAIL"} and rows[0]["app_version"] == "1.0"
    assert not list(out.glob("*.tmp"))
    assert export_table.run(["--day", DAY, "--format", "csv", "--out", str(out)], settings) == 0
    assert "already exported" in capsys.readouterr().out
    assert export_table.run(["--day", DAY, "--format", "csv", "--out", str(out), "--force"], settings) == 0


def test_default_day_is_yesterday(settings, tmp_path):
    _seed(settings)
    assert export_table.run(["--format", "csv", "--out", str(tmp_path)], settings) == 0
    assert (tmp_path / f"inspections_{DAY}.csv.gz").exists()


def test_parquet_roundtrip(settings, tmp_path):
    pq = pytest.importorskip("pyarrow.parquet")
    _seed(settings)
    assert export_table.run(["--day", DAY, "--out", str(tmp_path)], settings) == 0
    t = pq.read_table(tmp_path / f"inspections_{DAY}.parquet")
    assert t.num_rows == 30 and "tool1_value" in t.column_names
    assert t.schema.field("score").type == "double" and sorted(set(t["status"].to_pylist())) == ["FAIL", "PASS"]


def test_parquet_falls_back_to_csv_without_the_library(settings, tmp_path, monkeypatch, capsys):
    _seed(settings)
    monkeypatch.setattr(export_table, "ensure_pyarrow", lambda: "no internet")
    assert export_table.run(["--day", DAY, "--out", str(tmp_path)], settings) == 0
    assert "writing gzip CSV" in capsys.readouterr().out and (tmp_path / f"inspections_{DAY}.csv.gz").exists()


def test_bad_options_and_empty_day(settings, tmp_path, capsys):
    assert export_table.run(["--bogus"], settings) == 2
    assert export_table.run(["--day", "x"], settings) == 2
    assert export_table.run(["--format", "xls"], settings) == 2
    assert export_table.run(["--from", "2026-10-09", "--to", "2026-10-08"], settings) == 2
    assert export_table.run(["--day", "2020-01-01", "--format", "csv", "--out", str(tmp_path)], settings) == 1
    assert "no inspections" in capsys.readouterr().out and not list(tmp_path.glob("inspections_*"))
