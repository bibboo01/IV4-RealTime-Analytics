"""
Production metrics for reports / presentations.

    python -m scripts.metrics                          # today, per hour
    python -m scripts.metrics --from 2026-10-01 --to 2026-10-08 --by day
    python -m scripts.metrics --by day --csv report.csv   # open in Excel

Times are sensor local time. --to is exclusive.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, timedelta

from app.config import load_settings
from app.database.repository import DatabaseRepository
from app.metrics import summarize, tool_summary


def _fmt(v, suffix=""):
    return "-" if v is None else f"{v:,}{suffix}" if isinstance(v, int) else f"{v:.2f}{suffix}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", default=date.today().isoformat())
    ap.add_argument("--to", dest="end", default=(date.today() + timedelta(days=1)).isoformat())
    ap.add_argument("--by", choices=["hour", "day", "month"], default="hour")
    ap.add_argument("--program", type=int)
    ap.add_argument("--csv", help="write period table to this CSV file")
    a = ap.parse_args()

    repo = DatabaseRepository(load_settings().database_path)
    try:
        rows = summarize(repo, a.start, a.end, by=a.by, program_no=a.program)
        tools = tool_summary(repo, a.start, a.end, program_no=a.program)
    finally:
        repo.dispose()

    print(f"Period {a.start} .. {a.end} (by {a.by})")
    hdr = f"{'period':16} {'total':>10} {'NG':>8} {'NG %':>8} {'yield %':>8} {'avg ms':>7} {'max ms':>7} {'missing':>9}"
    print(hdr)
    print("-" * len(hdr))
    tot = {"total": 0, "ng": 0}
    for r in rows:
        tot["total"] += r.total
        tot["ng"] += r.fail_count
        print(f"{r.period:16} {_fmt(r.total):>10} {_fmt(r.fail_count):>8} {_fmt(r.ng_pct):>8} "
              f"{_fmt(r.yield_pct):>8} {_fmt(r.avg_time_ms):>7} {_fmt(r.max_time_ms):>7} {_fmt(r.missing):>9}")
    if tot["total"]:
        print("-" * len(hdr))
        print(f"{'TOTAL':16} {tot['total']:>10,} {tot['ng']:>8,} {100 * tot['ng'] / tot['total']:>8.2f}")

    if tools:
        print("\nNG by tool")
        for t in tools:
            print(f"  Tool{t.tool_no:02d} {t.tool_name or '':24} NG {t.ng_count:,}/{t.count:,} ({_fmt(t.ng_pct, '%')})"
                  f"  avg value {_fmt(t.avg_value)}  lowest OK value {_fmt(t.min_ok_value)}")

    if a.csv:
        with open(a.csv, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].to_dict()) if rows else ["period"])
            w.writeheader()
            for r in rows:
                w.writerow(r.to_dict())
        print(f"\nCSV written: {a.csv}")


if __name__ == "__main__":
    main()
