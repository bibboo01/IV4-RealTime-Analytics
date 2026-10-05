"""
Production metrics from the hourly tables (fast at any data volume).

    from app.metrics import summarize, tool_summary
    summarize(repo, "2026-10-03", "2026-10-04", by="hour")

Definitions
-----------
total        inspections received (files processed)
yield_pct    pass / total
ng_pct       fail / total
expected     sensor Trigger No. range in the period (trigger_max - trigger_min + 1)
             -- only when the counter did not reset in that period
missing      expected - total  (files the sensor counted but we never received;
             0 means FTP delivered everything). Computed per sensor per hour,
             so two sensors' counters never mix.
active_hours hours in the period that had at least one inspection
             (= how long the line actually ran; useful when hours vary per day)
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from sqlalchemy import text

from app.database.repository import DatabaseRepository


@dataclass
class PeriodStats:
    period: str
    sensor_id: str | None
    total: int
    pass_count: int
    fail_count: int
    unknown_count: int
    yield_pct: float | None
    ng_pct: float | None
    avg_time_ms: float | None
    max_time_ms: int | None
    expected: int | None
    missing: int | None
    active_hours: int

    def to_dict(self):
        return asdict(self)


@dataclass
class ToolStats:
    sensor_id: str | None
    tool_no: int
    tool_name: str | None
    count: int
    ng_count: int
    ng_pct: float | None
    avg_value: float | None
    min_value: float | None
    min_ok_value: float | None   # lowest value that still passed = margin to the NG limit

    def to_dict(self):
        return asdict(self)


def _pct(a: int, b: int) -> float | None:
    return round(100.0 * a / b, 3) if b else None


_PERIOD = {"hour": "hour", "day": "substr(hour, 1, 10)", "month": "substr(hour, 1, 7)"}


def summarize(
    repo: DatabaseRepository,
    start: str | None = None,
    end: str | None = None,
    by: str = "day",
    program_no: int | None = None,
    sensor_id: str | None = None,
    per_sensor: bool = False,
) -> list[PeriodStats]:
    """
    start/end: 'YYYY-MM-DD' or 'YYYY-MM-DD HH' (sensor local time), end exclusive.
    per_sensor=True returns one row per (period, sensor); otherwise sensors are summed.
    """
    period = _PERIOD[by]
    where, params = _filters(start, end, program_no, sensor_id)
    sensor_col = "sensor_id" if per_sensor else "NULL"
    # expected/missing are computed per hour (where the trigger range is meaningful), then summed
    sql = f"""
        SELECT {period} AS period, {sensor_col} AS sensor,
               SUM(total), SUM(pass_count), SUM(fail_count), SUM(unknown_count),
               SUM(time_ms_sum), SUM(time_ms_count), MAX(time_ms_max),
               SUM(CASE WHEN trigger_resets = 0 AND trigger_min IS NOT NULL
                        THEN trigger_max - trigger_min + 1 END),
               SUM(CASE WHEN trigger_resets > 0 OR trigger_min IS NULL THEN 1 ELSE 0 END),
               COUNT(DISTINCT CASE WHEN total > 0 THEN hour END)
        FROM hourly_stats {where}
        GROUP BY period, sensor ORDER BY period, sensor
    """
    out = []
    with repo.engine.connect() as conn:
        for row in conn.execute(text(sql), params):
            p, sensor, total, ok, ng, unk, tsum, tcnt, tmax, expected, unknown_hours, active = row
            if unknown_hours:
                expected = None
            out.append(PeriodStats(
                period=p, sensor_id=sensor, total=total, active_hours=active, pass_count=ok, fail_count=ng, unknown_count=unk,
                yield_pct=_pct(ok, total), ng_pct=_pct(ng, total),
                avg_time_ms=round(tsum / tcnt, 2) if tcnt else None, max_time_ms=tmax,
                expected=expected, missing=(max(expected - total, 0) if expected is not None else None),
            ))
    return out


def tool_summary(
    repo: DatabaseRepository,
    start: str | None = None,
    end: str | None = None,
    program_no: int | None = None,
    sensor_id: str | None = None,
    per_sensor: bool = False,
) -> list[ToolStats]:
    where, params = _filters(start, end, program_no, sensor_id)
    sensor_col = "sensor_id" if per_sensor else "NULL"
    sql = f"""
        SELECT {sensor_col} AS sensor, tool_no, MAX(tool_name), SUM(count), SUM(ng_count),
               SUM(value_sum), SUM(value_count), MIN(value_min), MIN(ok_value_min)
        FROM hourly_tool_stats {where}
        GROUP BY sensor, tool_no ORDER BY sensor, tool_no
    """
    out = []
    with repo.engine.connect() as conn:
        for sensor, no, name, cnt, ng, vsum, vcnt, vmin, okmin in conn.execute(text(sql), params):
            out.append(ToolStats(
                sensor_id=sensor, tool_no=no, tool_name=name, count=cnt, ng_count=ng, ng_pct=_pct(ng, cnt),
                avg_value=round(vsum / vcnt, 3) if vcnt else None, min_value=vmin, min_ok_value=okmin,
            ))
    return out


def _filters(start, end, program_no, sensor_id):
    clauses, params = [], {}
    if start:
        clauses.append("hour >= :start")
        params["start"] = start
    if end:
        clauses.append("hour < :end")
        params["end"] = end
    if program_no is not None:
        clauses.append("program_no = :prog")
        params["prog"] = program_no
    if sensor_id is not None:
        clauses.append("sensor_id = :sensor")
        params["sensor"] = sensor_id
    return ("WHERE " + " AND ".join(clauses)) if clauses else "", params
