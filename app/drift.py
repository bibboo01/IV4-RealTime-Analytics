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


def find_drift(repo, now: datetime, pct: float, min_count: int) -> list[Drift]:
    """Tools whose recent average score moved by >= pct % (either way), or whose NG rate doubled."""
    if pct <= 0:
        return []
    split = now - timedelta(hours=RECENT_HOURS - 1)          # recent = current hour and the one before
    recent = _window(repo, _hour(split), _hour(now + timedelta(hours=1)))
    base = _window(repo, _hour(split - timedelta(days=BASELINE_DAYS)), _hour(split))
    out: list[Drift] = []
    for key, (name, cnt, ng, vsum, vcnt) in recent.items():
        b = base.get(key)
        if not b or cnt < min_count or b[1] < MIN_BASELINE:
            continue
        _, bcnt, bng, bvsum, bvcnt = b[0], b[1], b[2], b[3], b[4]
        sensor, program, tool = key
        if vcnt and bvcnt and bvsum:
            r_avg, b_avg = vsum / vcnt, bvsum / bvcnt
            if b_avg and abs(r_avg - b_avg) / abs(b_avg) * 100 >= pct:
                out.append(Drift(sensor, program, tool, name, "score", b_avg, r_avg, cnt))
        r_ng, b_ng = ng / cnt * 100, bng / bcnt * 100
        if r_ng >= 2 * b_ng and r_ng - b_ng >= 1:      # doubled, and at least 1 point (lines run at ~0.5% NG)
            out.append(Drift(sensor, program, tool, name, "ng", b_ng, r_ng, cnt))
    return out


def run(args: list[str], s, _running_pid=None) -> int:
    from app.database.repository import DatabaseRepository
    repo = DatabaseRepository(s.database_path)
    try:
        found = find_drift(repo, datetime.now(), s.drift_pct, s.drift_min)
    finally:
        repo.dispose()
    if s.drift_pct <= 0:
        print("Drift check is off (IV4_DRIFT_PCT=0).")
        return 0
    if not found:
        print(f"No drift: every tool is within {s.drift_pct:g}% of its last {BASELINE_DAYS} days "
              f"(needs {MIN_BASELINE:,}+ past and {s.drift_min:,}+ recent inspections per tool).")
        return 0
    for d in found:
        print(" ! " + d.describe())
    return 0
