"""Regenerate deploy/grafana/iv4-dashboard.json (Grafana + frser-sqlite-datasource).

    python deploy/grafana/build_dashboard.py

Hours are stored as sensor local time, so the dashboard timezone is fixed to UTC: the
axis then shows the sensor's own clock. Queries only read the pre-aggregated hourly_* tables.
"""
from __future__ import annotations

import json
from pathlib import Path

DS = {"type": "frser-sqlite-datasource", "uid": "iv4-sqlite"}
RANGE = "CAST(strftime('%s', hour || ':00:00') AS INTEGER) BETWEEN $__from / 1000 AND $__to / 1000"
T = "CAST(strftime('%s', hour || ':00:00') AS INTEGER)"

QUERIES = {
    "throughput": f"SELECT {T} AS time, SUM(total) AS total, SUM(fail_count) AS NG "
                  f"FROM hourly_stats WHERE {RANGE} GROUP BY hour ORDER BY hour",
    "ngpct": f"SELECT {T} AS time, ROUND(100.0 * SUM(fail_count) / SUM(total), 2) AS ng_pct "
             f"FROM hourly_stats WHERE {RANGE} GROUP BY hour ORDER BY hour",
    "tool_ng": f"SELECT {T} AS time, tool_no || ' ' || COALESCE(tool_name, '') AS tool, "
               f"ROUND(100.0 * SUM(ng_count) / SUM(count), 2) AS ng_pct "
               f"FROM hourly_tool_stats WHERE {RANGE} GROUP BY hour, tool_no ORDER BY hour",
    "total": "SELECT SUM(total) AS inspected FROM hourly_stats "
             "WHERE hour >= strftime('%Y-%m-%d', 'now') ",
    "ngtotal": f"SELECT ROUND(100.0 * SUM(fail_count) / MAX(SUM(total), 1), 2) AS ng_pct "
               f"FROM hourly_stats WHERE {RANGE}",
    "missing": f"SELECT hour, sensor_id, total, trigger_max - trigger_min + 1 - total AS missing "
               f"FROM hourly_stats WHERE trigger_resets = 0 AND trigger_max IS NOT NULL AND {RANGE} "
               f"AND trigger_max - trigger_min + 1 - total > 0 ORDER BY hour DESC LIMIT 200",
}


def panel(pid, title, kind, x, y, w, h, sql, fmt="time_series", unit=None, transforms=None):
    p = {
        "id": pid, "title": title, "type": kind, "datasource": DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": [{"refId": "A", "datasource": DS, "queryText": sql, "rawQueryText": sql,
                     "queryType": fmt, "timeColumns": ["time"]}],
        "fieldConfig": {"defaults": ({"unit": unit} if unit else {}), "overrides": []},
    }
    if transforms:
        p["transformations"] = transforms
    return p


def build() -> dict:
    split = [{"id": "partitionByValues", "options": {"fields": ["tool"]}}]
    panels = [
        panel(1, "Inspected today", "stat", 0, 0, 6, 4, QUERIES["total"], "table"),
        panel(2, "NG % (selected range)", "stat", 6, 0, 6, 4, QUERIES["ngtotal"], "table", "percent"),
        panel(3, "Inspections per hour", "timeseries", 0, 4, 12, 8, QUERIES["throughput"]),
        panel(4, "NG % per hour", "timeseries", 12, 4, 12, 8, QUERIES["ngpct"], unit="percent"),
        panel(5, "NG % per tool per hour", "timeseries", 0, 12, 24, 9, QUERIES["tool_ng"],
              unit="percent", transforms=split),
        panel(6, "Missing files (counter gaps)", "table", 0, 21, 24, 8, QUERIES["missing"], "table"),
    ]
    return {
        "uid": "iv4-overview", "title": "IV4 Data Agent", "timezone": "utc", "schemaVersion": 38,
        "version": 1, "refresh": "1m", "time": {"from": "now-24h", "to": "now"},
        "tags": ["iv4"], "panels": panels,
    }


if __name__ == "__main__":
    out = Path(__file__).with_name("iv4-dashboard.json")
    out.write_text(json.dumps(build(), indent=2), encoding="utf-8")
    print("wrote", out)
