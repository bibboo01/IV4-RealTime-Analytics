"""
`run report [YYYY-MM-DD] [--out file.html]` - one-page daily report (open in a browser, print to PDF, or send).
`run report --excel [range]` - Excel workbook with real charts and a written summary.

HTML: default is today. Saved to reports/report-<date>.html next to the program (never touched by run update/clear).

Excel (reports/report-<from>_<to>.xlsx):
    run report --excel                 the last 7 days (today included)
    run report --excel 2026-10-09      one day
    run report --excel --days 30       the last 30 days
    run report --excel --from 2026-10-01 --to 2026-10-09
    run report --excel --all           everything recorded
    options: --images N   put the N newest NG pictures in the file (default 6, 0 = none)
             --lang en    English text (default: Thai)      --out file.xlsx
Needs the 'openpyxl' library; it is installed once, the first time (internet needed). It reads the hourly
tables only, so it is light even on a large database.
"""
from __future__ import annotations

import html
from datetime import datetime, timedelta
from pathlib import Path

from app.config import BASE_DIR, Settings
from app.metrics import summarize, tool_summary

CSS = """body{font-family:Segoe UI,Arial,sans-serif;margin:24px auto;max-width:960px;color:#1f2933;padding:0 16px}
h1{margin:0 0 4px}h2{margin:28px 0 8px;font-size:18px;border-bottom:2px solid #e4e7eb;padding-bottom:4px}
.sub{color:#616e7c;margin-bottom:16px}.kpis{display:flex;flex-wrap:wrap;gap:12px}
.kpi{flex:1 1 140px;border:1px solid #e4e7eb;border-radius:8px;padding:10px 14px}.kpi b{display:block;font-size:24px}
.kpi span{color:#616e7c;font-size:13px}table{border-collapse:collapse;width:100%;font-size:14px}
th,td{padding:5px 8px;border-bottom:1px solid #e4e7eb;text-align:right}th:first-child,td:first-child{text-align:left}
th{background:#f5f7fa}.bar{display:inline-block;height:10px;background:#e0645c;border-radius:2px;vertical-align:middle}
.warn{background:#fff4e5;border-left:4px solid #f0a020;padding:8px 12px;margin:6px 0}.ok{color:#1b7f4b}"""


def _n(v) -> str:
    return "-" if v is None else f"{v:,}"


def _p(v) -> str:
    return "-" if v is None else f"{v:.2f}%"


def _table(head: list[str], rows: list[list[str]]) -> str:
    th = "".join(f"<th>{html.escape(h)}</th>" for h in head)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><tr>{th}</tr>{body}</table>"


def build(repo, s: Settings, day: str, now: datetime | None = None) -> str:
    now = now or datetime.now()
    nxt = (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    tot = summarize(repo, day, nxt, by="day")
    t = tot[0] if tot else None
    per_sensor = summarize(repo, day, nxt, by="day", per_sensor=True)
    hours = summarize(repo, day, nxt, by="hour")
    tools = sorted(tool_summary(repo, day, nxt, per_sensor=True), key=lambda x: -(x.ng_count or 0))
    e = html.escape
    parts = [f"<h1>IV4 Daily Report - {e(day)}</h1>",
             f'<div class="sub">Generated {now:%Y-%m-%d %H:%M} | agent version {e(_version())}</div>']
    if not t:
        parts.append("<p>No inspections recorded on this day.</p>")
        return _page(day, parts)
    kp = [(_n(t.total), "inspections"), (_p(t.yield_pct), "yield (OK)"), (_p(t.ng_pct), "NG"),
          (_n(t.missing) + ("*" if t.unknown_hours else ""), "missing files"),
          (f"{t.avg_time_ms:.1f} ms" if t.avg_time_ms else "-", "avg inspection time"), (str(t.active_hours), "hours with data")]
    parts.append('<div class="kpis">' + "".join(f'<div class="kpi"><b>{e(v)}</b><span>{e(k)}</span></div>' for v, k in kp) + "</div>")
    notes = []
    if t.ng_pct is not None and s.ng_alert_pct and t.ng_pct >= s.ng_alert_pct:
        notes.append(f"Day NG {t.ng_pct:.1f}% is above the alert level {s.ng_alert_pct:g}%.")
    if t.missing:
        notes.append(f"{t.missing:,} files were counted by the sensor but never arrived (FTP / trigger interval)."
                     + (" '*' = some hours could not be measured (counter reset)." if t.unknown_hours else ""))
    bad = [r for r in hours if r.total >= s.ng_alert_min and (r.ng_pct or 0) >= max(s.ng_alert_pct, 0.01) and s.ng_alert_pct]
    for r in bad:
        notes.append(f"{r.period[-2:]}:00 NG {r.ng_pct:.1f}% ({r.fail_count:,} of {r.total:,}).")
    parts.append("<h2>Attention</h2>" + ("".join(f'<div class="warn">{e(n)}</div>' for n in notes)
                                         or '<p class="ok">Nothing abnormal.</p>'))
    if len(per_sensor) > 1:
        parts.append("<h2>By sensor</h2>" + _table(
            ["Sensor", "Inspections", "Yield", "NG", "Missing"],
            [[e(str(r.sensor_id)), _n(r.total), _p(r.yield_pct), _p(r.ng_pct), _n(r.missing)] for r in per_sensor]))
    top = max((r.ng_pct or 0 for r in hours), default=0) or 1
    parts.append("<h2>By hour</h2>" + _table(
        ["Hour", "Inspections", "NG", "NG %", "", "Missing"],
        [[f"{r.period[-2:]}:00", _n(r.total), _n(r.fail_count), _p(r.ng_pct),
          f'<span class="bar" style="width:{int(120 * (r.ng_pct or 0) / top)}px"></span>', _n(r.missing)] for r in hours]))
    if tools:
        parts.append("<h2>Tools (most NG first)</h2>" + _table(
            ["Sensor / tool", "Count", "NG", "NG %", "Avg score", "Lowest OK score"],
            [[e(f"{r.sensor_id} #{r.tool_no} {r.tool_name or ''}".strip()), _n(r.count), _n(r.ng_count), _p(r.ng_pct),
              "-" if r.avg_value is None else f"{r.avg_value:.1f}", "-" if r.min_ok_value is None else f"{r.min_ok_value:.0f}"]
             for r in tools[:12]]))
    return _page(day, parts)


def _version() -> str:
    from app.version import read_version
    return read_version()


def _page(day: str, parts: list[str]) -> str:
    return (f'<!doctype html><html><head><meta charset="utf-8"><title>IV4 report {day}</title>'
            f'<style>{CSS}</style></head><body>{"".join(parts)}</body></html>')


def _excel(args: list[str], s: Settings, base: Path | None) -> int:
    from app import report_xlsx
    from app.database.repository import DatabaseRepository
    opts = {"days": None, "from": None, "to": None, "images": 6, "lang": "th", "out": None, "all": False, "day": None}
    it = iter(args)
    try:
        for a in it:
            if a == "--excel":
                continue
            if a == "--all":
                opts["all"] = True
            elif a in ("--days", "--images"):
                opts[a[2:]] = int(next(it))
            elif a in ("--from", "--to", "--lang", "--out"):
                opts[a[2:]] = next(it)
            elif a.startswith("--"):
                print(f"Unknown option {a}")
                return 2
            else:
                opts["day"] = a
    except (StopIteration, ValueError):
        print("Missing or invalid value for an option")
        return 2
    if opts["lang"] not in report_xlsx.TXT:
        print("--lang must be th or en")
        return 2
    today = datetime.now().date()
    try:
        if opts["day"]:
            d0 = datetime.strptime(opts["day"], "%Y-%m-%d").date()
            start, end = d0, d0 + timedelta(days=1)
        elif opts["from"] or opts["to"]:
            start = datetime.strptime(opts["from"], "%Y-%m-%d").date() if opts["from"] else today - timedelta(days=6)
            end = (datetime.strptime(opts["to"], "%Y-%m-%d").date() if opts["to"] else today) + timedelta(days=1)
        elif opts["days"]:
            start, end = today - timedelta(days=max(opts["days"], 1) - 1), today + timedelta(days=1)
        else:
            start, end = today - timedelta(days=6), today + timedelta(days=1)
    except ValueError:
        print("Dates must look like 2026-10-08")
        return 2
    err = report_xlsx.ensure_openpyxl()
    if err:
        print(f"Cannot make the Excel report: {err}")
        print("Install it by hand with:  python -m pip install openpyxl   (or use the HTML report: run report)")
        return 1
    repo = DatabaseRepository(s.database_path)
    try:
        if opts["all"]:
            from sqlalchemy import text
            with repo.engine.connect() as c:
                first = c.execute(text("SELECT MIN(hour) FROM hourly_stats")).scalar()
            start = datetime.strptime(first[:10], "%Y-%m-%d").date() if first else today
            end = today + timedelta(days=1)
        a, b = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
        data = report_xlsx.collect(repo, s, a, b, images=max(opts["images"], 0))
    finally:
        repo.dispose()
    if not data.hours:
        print(f"No inspections recorded between {a} and {(end - timedelta(days=1)):%Y-%m-%d}.")
        return 1
    last = (end - timedelta(days=1)).strftime("%Y-%m-%d")
    path = Path(opts["out"]) if opts["out"] else (base or BASE_DIR) / "reports" / f"report-{a}_{last}.xlsx"
    report_xlsx.write(data, s, path, opts["lang"])
    print(f"Excel report saved: {path}   ({len(data.hours)} hours, {sum(h.total for h in data.hours):,} inspections)")
    return 0


def run(args: list[str], s: Settings, _running_pid=None, base: Path | None = None) -> int:
    from app.database.repository import DatabaseRepository
    if "-h" in args or "--help" in args:
        print(__doc__)
        return 0
    if "--excel" in args:
        return _excel(args, s, base)
    out = None
    rest = []
    it = iter(args)
    for a in it:
        if a == "--out":
            out = next(it, None)
        else:
            rest.append(a)
    day = rest[0] if rest else datetime.now().strftime("%Y-%m-%d")
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        print("Date must look like 2026-10-08")
        return 2
    repo = DatabaseRepository(s.database_path)
    try:
        page = build(repo, s, day)
    finally:
        repo.dispose()
    path = Path(out) if out else (base or BASE_DIR) / "reports" / f"report-{day}.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")
    print(f"Report saved: {path}   (open it in a browser; Ctrl+P = save as PDF)")
    return 0
