"""
Quick production summary from the database.

    run status   (or run metrics)            # today + all time
"""
from __future__ import annotations

import sqlite3

from app.config import load_settings


def main() -> None:
    s = load_settings()
    con = sqlite3.connect(f"file:{s.database_path}?mode=ro", uri=True)
    q = """
        SELECT analysis_status, COUNT(*) FROM inspection
        WHERE (:day IS NULL OR date(created_at) = date('now'))
        GROUP BY analysis_status ORDER BY 2 DESC
    """
    for label, day in (("Today (UTC)", 1), ("All time", None)):
        rows = con.execute(q, {"day": day}).fetchall()
        total = sum(n for _, n in rows)
        print(f"{label}: {total} inspections")
        for status, n in rows:
            pct = 100 * n / total if total else 0
            print(f"  {status or '-':8} {n:8d}  {pct:5.1f}%")
    con.close()


if __name__ == "__main__":
    main()
