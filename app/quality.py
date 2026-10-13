"""Data quality check: is what the agent stored complete and believable?

    run quality              last 24 hours, printed and saved to reports\\quality-<date>.txt
    run quality --days 3     look further back
    run quality --no-save    only print

Exit code 0 = nothing to worry about, 1 = something found (the schedulable form of "is the data good?").
Works from the hourly summary tables, so it is cheap on a big database. One extra indexed count on the
inspection table covers rows with a missing result.

Checks, per closed hour (the hour still running is only checked for things that cannot be a backlog: unknown, missing as INFO, NG spike):
  volume    far fewer inspections than this line normally does (sensor stopped, or files lost)
  missing   the sensor's trigger counter jumped: files never arrived (the running hour may just be waiting)
  unknown   results the agent could not read
  ng spike  NG% far above the normal for this line
  tools     a tool reported for fewer pieces than were inspected (tool results lost or unparsed)
  values    scores outside 0-100 (parser or sensor-setting problem)
  speed     processing time far above normal
  clock     hours stamped in the future (the sensor clock is ahead of the PC)
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import text

from app.config import BASE_DIR

LOW_VOLUME = 0.90          # of the normal hourly volume
NG_SPIKE_MIN_PCT = 5.0     # and at least this many times the normal NG%...
NG_SPIKE_TIMES = 5
NG_SPIKE_MIN_TOTAL = 200
TOOL_COVERAGE = 0.99
SLOW_TIMES = 2.0
SEV = {"ERR": 0, "WARN": 1, "INFO": 2}


@dataclass
class Finding:
    severity: str
    check: str
    hour: str
    message: str

    def line(self) -> str:
        return f"[{self.severity:4}] {self.hour or '-':13} {self.check:8} {self.message}"


def _median(vals: list[float]) -> float | None:
    return statistics.median(vals) if vals else None


def check(repo, now: datetime, days: float = 1.0) -> tuple[list[Finding], dict]:
    start = (now - timedelta(days=days)).strftime("%Y-%m-%d %H")
    cur = now.strftime("%Y-%m-%d %H")
    with repo.engine.connect() as c:
        hours = [dict(r._mapping) for r in c.execute(text(
            """SELECT hour, SUM(total) AS total, SUM(fail_count) AS ng, SUM(unknown_count) AS unknown,
                      SUM(time_ms_sum) AS ms_sum, SUM(time_ms_count) AS ms_n,
                      SUM(CASE WHEN trigger_resets = 0 AND trigger_min IS NOT NULL
                               THEN MAX(trigger_max - trigger_min + 1 - total, 0) END) AS missing,
                      SUM(trigger_resets) AS resets
               FROM hourly_stats WHERE hour >= :a GROUP BY hour ORDER BY hour"""), {"a": start})]
        tools = [dict(r._mapping) for r in c.execute(text(
            """SELECT hour, tool_no, MAX(tool_name) AS name, SUM(count) AS n, SUM(value_sum) AS vsum,
                      SUM(value_count) AS vn, MIN(value_min) AS vmin
               FROM hourly_tool_stats WHERE hour >= :a GROUP BY hour, tool_no"""), {"a": start})]
    out: list[Finding] = []
    stats = {"hours": len(hours), "total": sum(h["total"] or 0 for h in hours), "ng": sum(h["ng"] or 0 for h in hours)}
    if not hours:
        out.append(Finding("ERR", "volume", "", f"no inspections stored in the last {days:g} day(s)"))
        return out, stats

    closed = [h for h in hours if h["hour"] < cur]
    full = [h for h in closed if (h["total"] or 0) > 0]
    # "normal" is learned from the busiest half of the hours so one stop does not become the new normal
    top = sorted((h["total"] for h in full), reverse=True)[: max(len(full) // 2, 1)]
    normal_vol = _median(top)
    ng_rates = [100.0 * h["ng"] / h["total"] for h in full if h["total"]]
    normal_ng = _median(ng_rates) or 0.0
    ms = [h["ms_sum"] / h["ms_n"] for h in full if h["ms_n"]]
    normal_ms = _median(ms)
    stats.update(normal_hourly_volume=normal_vol, normal_ng_pct=round(normal_ng, 2))

    # an hour with a gap in the middle of the range (no row at all) is the clearest "sensor stopped"
    have = {h["hour"] for h in hours}
    t = datetime.strptime(start, "%Y-%m-%d %H")
    while t.strftime("%Y-%m-%d %H") < cur:
        k = t.strftime("%Y-%m-%d %H")
        if k not in have and k > min(have):
            out.append(Finding("WARN", "volume", k, "no inspections at all this hour (sensor or agent stopped; ignore if the line was off)"))
        t += timedelta(hours=1)

    for h in hours:
        k, tot = h["hour"], h["total"] or 0
        running = k >= cur
        if k > (now + timedelta(hours=1)).strftime("%Y-%m-%d %H"):
            out.append(Finding("WARN", "clock", k, "hour is in the future: sensor clock is ahead of this PC"))
        if h["unknown"]:
            out.append(Finding("ERR", "unknown", k, f"{h['unknown']:,} inspection(s) with an unreadable result"))
        if h["missing"]:
            sev = "INFO" if running else ("ERR" if h["missing"] >= 10 else "WARN")
            why = " (hour still running: may be files waiting)" if running else ""
            out.append(Finding(sev, "missing", k, f"{h['missing']:,} file(s) counted by the sensor never arrived{why}"))
        if not running:
            if normal_vol and tot < LOW_VOLUME * normal_vol:
                out.append(Finding("WARN", "volume", k, f"{tot:,} inspections, normal ~{normal_vol:,.0f} "
                                                        f"({normal_vol - tot:,.0f} short, ~{(normal_vol - tot) / 3.33 / 60:.0f} min of sensor time)"))
        if tot >= NG_SPIKE_MIN_TOTAL:
            pct = 100.0 * h["ng"] / tot
            if pct >= NG_SPIKE_MIN_PCT and pct >= NG_SPIKE_TIMES * max(normal_ng, 0.1):
                out.append(Finding("ERR" if pct >= 20 else "WARN", "ng spike", k,
                                   f"NG {pct:.1f}% ({h['ng']:,}) vs normal {normal_ng:.2f}% - check the images and the light before blaming the parts"))
        if running:
            continue
        if h["ms_n"] and normal_ms and h["ms_sum"] / h["ms_n"] > SLOW_TIMES * normal_ms:
            out.append(Finding("WARN", "speed", k, f"average {h['ms_sum'] / h['ms_n']:.0f} ms vs normal {normal_ms:.0f} ms"))
    totals = {h["hour"]: h["total"] or 0 for h in hours}
    for r in tools:
        tot = totals.get(r["hour"], 0)
        who = f"tool {r['tool_no']}" + (f" ({r['name']})" if r["name"] else "")
        if r["hour"] < cur and tot and (r["n"] or 0) < TOOL_COVERAGE * tot:
            out.append(Finding("WARN", "tools", r["hour"], f"{who} reported for {r['n']:,} of {tot:,} pieces"))
        avg = r["vsum"] / r["vn"] if r["vn"] else None
        if (r["vmin"] is not None and r["vmin"] < 0) or (avg is not None and avg > 100):
            out.append(Finding("WARN", "values", r["hour"], f"{who}: scores outside 0-100 (min {r['vmin']}, avg {avg if avg is not None else 0:.1f})"))

    since_utc = datetime.utcnow() - timedelta(days=days)
    with repo.engine.connect() as c:
        bad = c.execute(text(
            "SELECT COUNT(*) FROM inspection WHERE created_at >= :a AND (analysis_status IS NULL OR timestamp IS NULL)"),
            {"a": since_utc}).scalar() or 0
    if bad:
        out.append(Finding("WARN", "unknown", "", f"{bad:,} stored row(s) without a result status or timestamp"))
    out.sort(key=lambda f: (SEV[f.severity], f.hour))
    return out, stats


def run(args: list[str], s, _running_pid=None, base: Path | None = None) -> int:
    from app.database.repository import DatabaseRepository
    days, save = 1.0, True
    it = iter(args)
    for a in it:
        if a == "--days":
            try:
                days = float(next(it))
                if days <= 0:
                    raise ValueError
            except (StopIteration, ValueError):
                print("--days needs a positive number")
                return 2
        elif a == "--no-save":
            save = False
        else:
            print(__doc__)
            return 0 if a in ("-h", "--help") else 2
    repo = DatabaseRepository(s.database_path)
    now = datetime.now()
    try:
        found, st = check(repo, now, days)
    finally:
        repo.dispose()
    head = (f"Data quality, last {days:g} day(s) up to {now:%Y-%m-%d %H:%M}: {st['hours']} hour(s), "
            f"{st['total']:,} inspections, NG {100 * st['ng'] / max(st['total'], 1):.2f}%")
    lines = [head] + ([f"  normal: ~{st['normal_hourly_volume']:,.0f}/hour, NG {st['normal_ng_pct']}%"]
                      if "normal_hourly_volume" in st and st["normal_hourly_volume"] else [])
    lines += [f.line() for f in found] or ["  all checks passed."]
    n_bad = sum(1 for f in found if f.severity != "INFO")
    lines.append(f"Result: {'OK' if not n_bad else str(n_bad) + ' finding(s) to look at'}")
    print("\n".join(lines))
    if save:
        d = (base or BASE_DIR) / "reports"
        try:
            d.mkdir(parents=True, exist_ok=True)
            (d / f"quality-{now:%Y-%m-%d}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError as exc:
            print(f"(could not save the report: {exc})")
    return 1 if n_bad else 0
