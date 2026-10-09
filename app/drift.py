"""Drift check: is a tool's score (or NG rate) moving away from what is normal for this line?

Compares the last RECENT_HOURS with the BASELINE_DAYS before them, per sensor / program / tool, from
hourly_tool_stats. A drop in the average score usually shows up before NG rises (dirty lens, light fading,
a new material lot), so it is an early warning.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import text

RECENT_HOURS = 2
BASELINE_DAYS = 7
MIN_BASELINE = 2000      # inspections needed before "normal" means anything


@dataclass
class Drift:
    sensor_id: str
    program_no: int
    tool_no: int
    tool_name: str | None
    kind: str            # 'score' | 'ng'
    baseline: float
    recent: float
    count: int           # recent inspections behind it

    @property
    def change_pct(self) -> float:
        return (self.recent - self.baseline) / self.baseline * 100 if self.baseline else 0.0

    def describe(self) -> str:
        who = f"{self.sensor_id} tool {self.tool_no}" + (f" ({self.tool_name})" if self.tool_name else "")
        if self.kind == "score":
            return (f"{who}: average score {self.recent:.1f} vs normal {self.baseline:.1f} "
                    f"({self.change_pct:+.0f}%) over the last {RECENT_HOURS}h")
        return f"{who}: NG {self.recent:.1f}% vs normal {self.baseline:.1f}% over the last {RECENT_HOURS}h"


def _hour(d: datetime) -> str:
    return d.strftime("%Y-%m-%d %H")


def _window(repo, start: str, end: str) -> dict:
    sql = """SELECT sensor_id, program_no, tool_no, MAX(tool_name), SUM(count), SUM(ng_count),
                    SUM(value_sum), SUM(value_count)
             FROM hourly_tool_stats WHERE hour >= :a AND hour < :b GROUP BY sensor_id, program_no, tool_no"""
    with repo.engine.connect() as c:
        return {(r[0], r[1], r[2]): r[3:] for r in c.execute(text(sql), {"a": start, "b": end})}


def compare(repo, now: datetime, min_count: int) -> list[dict]:
    """Every tool: recent vs normal, or why it cannot be compared yet."""
    split = now - timedelta(hours=RECENT_HOURS - 1)          # recent = current hour and the one before
    recent = _window(repo, _hour(split), _hour(now + timedelta(hours=1)))
    base = _window(repo, _hour(split - timedelta(days=BASELINE_DAYS)), _hour(split))
    rows = []
    for key, (name, cnt, ng, vsum, vcnt) in sorted(recent.items(), key=lambda kv: str(kv[0])):
        sensor, program, tool = key
        row = {"sensor": sensor, "program": program, "tool": tool, "name": name, "recent_n": cnt,
               "recent_ng": ng / cnt * 100 if cnt else None, "recent_avg": vsum / vcnt if vcnt else None,
               "base_n": 0, "base_ng": None, "base_avg": None, "skip": None}
        b = base.get(key)
        if b:
            row.update(base_n=b[1], base_ng=b[2] / b[1] * 100 if b[1] else None,
                       base_avg=b[3] / b[4] if b[4] else None)
        if cnt < min_count:
            row["skip"] = f"only {cnt:,} recent inspections (needs {min_count:,})"
        elif not b or b[1] < MIN_BASELINE:
            row["skip"] = f"only {(b[1] if b else 0):,} past inspections (needs {MIN_BASELINE:,})"
        rows.append(row)
    return rows


def find_drift(repo, now: datetime, pct: float, min_count: int) -> list[Drift]:
    """Tools whose recent average score moved by >= pct % (either way), or whose NG rate doubled."""
    if pct <= 0:
        return []
    out: list[Drift] = []
    for r in compare(repo, now, min_count):
        if r["skip"]:
            continue
        args = (r["sensor"], r["program"], r["tool"], r["name"])
        if r["recent_avg"] is not None and r["base_avg"]:
            if abs(r["recent_avg"] - r["base_avg"]) / abs(r["base_avg"]) * 100 >= pct:
                out.append(Drift(*args, "score", r["base_avg"], r["recent_avg"], r["recent_n"]))
        if r["recent_ng"] >= 2 * r["base_ng"] and r["recent_ng"] - r["base_ng"] >= 1:   # doubled and >= 1 point
            out.append(Drift(*args, "ng", r["base_ng"], r["recent_ng"], r["recent_n"]))
    return out


def run(args: list[str], s, _running_pid=None) -> int:
    from app.database.repository import DatabaseRepository
    repo = DatabaseRepository(s.database_path)
    now = datetime.now()
    try:
        found = find_drift(repo, now, s.drift_pct, s.drift_min)
        rows = compare(repo, now, s.drift_min)
    finally:
        repo.dispose()
    if s.drift_pct <= 0:
        print("Drift check is off (IV4_DRIFT_PCT=0).")
        return 0
    print(f"Last {RECENT_HOURS}h vs the {BASELINE_DAYS} days before (alert at {s.drift_pct:g}% score change or NG doubling):")
    if not rows:
        print("  no inspections in the last hours.")
    f = lambda v, d=1: "-" if v is None else f"{v:.{d}f}"   # noqa: E731
    for r in rows:
        who = f"{r['sensor']} tool {r['tool']} {r['name'] or ''}".strip()
        state = r["skip"] and f"cannot compare: {r['skip']}" or ("DRIFT" if any(
            (d.sensor_id, d.tool_no) == (r["sensor"], r["tool"]) for d in found) else "ok")
        print(f"  {who:34} score {f(r['recent_avg'])} (normal {f(r['base_avg'])})   "
              f"NG {f(r['recent_ng'], 2)}% (normal {f(r['base_ng'], 2)}%)   {state}")
    print("No drift." if not found else "")
    for d in found:
        print(" ! " + d.describe())
    return 0
