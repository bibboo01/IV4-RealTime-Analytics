"""
Live terminal monitor: one screen with everything the line needs at a glance.

    run monitor                 refresh every 2 s (Ctrl+C to quit)
    run monitor --interval 5    slower refresh
    run monitor --hours 12      longer hourly chart
    run monitor --live-hours 6  more rows in the per-minute live chart (default 3, 0 = hide)
    run monitor --once          print one snapshot and exit (no screen clearing)

Read-only: works while the agent (or the Windows service) is running, from a
second terminal or over Remote Desktop. Data comes from logs/health.json
(agent state, backlog) and the hourly tables (today's numbers), so a
refresh stays fast at any database size.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.config import Settings
from app.metrics import PeriodStats, miss_text, summarize, tool_summary

BAR = "█"
SPARK = "▁▂▃▄▅▆▇█"
LIVE_CACHE_S = 5          # the per-minute query is re-run at most this often
_live_cache: dict = {}
HEARTBEAT_STALE_S = 60
BACKLOG_WARN = 200          # files waiting in incoming/ (~5 s of 2 sensors)


# ------------------------------------------------------------------
# colour (ANSI; Windows 10+ console supports it once VT mode is on)
# ------------------------------------------------------------------

class Style:
    def __init__(self, enabled: bool):
        self.enabled = enabled

    def _c(self, code: str, s: str) -> str:
        return f"\033[{code}m{s}\033[0m" if self.enabled else s

    def bold(self, s): return self._c("1", s)
    def dim(self, s): return self._c("2", s)
    def red(self, s): return self._c("31;1", s)
    def green(self, s): return self._c("32;1", s)
    def yellow(self, s): return self._c("33;1", s)
    def cyan(self, s): return self._c("36", s)


def _enable_ansi() -> bool:
    if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
        return False
    if os.name == "nt":
        os.system("")           # switches the Windows console into VT (ANSI) mode
    return True


# ------------------------------------------------------------------
# data
# ------------------------------------------------------------------

@dataclass
class Snapshot:
    now: datetime
    pid: str | None
    health: dict | None
    today: list[PeriodStats] = field(default_factory=list)
    this_hour: list[PeriodStats] = field(default_factory=list)
    hours: list[PeriodStats] = field(default_factory=list)
    tools: list = field(default_factory=list)
    latest_ng: list[tuple] = field(default_factory=list)
    latest_hour: str | None = None
    last_minute: dict[str, int] = field(default_factory=dict)   # sensor -> inspections
    clock_offset_s: float | None = None     # sensor clock minus this PC's clock, from the newest inspection
    db_error: str | None = None
    chart_hours: int = 8
    live_hours: int = 3
    live: dict = field(default_factory=dict)    # 'YYYY-MM-DD HH' -> {minute: (total, ng)}


def _read_health(s: Settings) -> dict | None:
    try:
        return json.loads((s.log_dir / "health.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _clock_offset(row, now: datetime) -> float | None:
    """Sensor clock minus this PC's clock (seconds), using when the newest inspection was received."""
    if not row or not row[0] or not row[1]:
        return None
    try:
        sensor = datetime.fromisoformat(str(row[0])[:19].replace("/", "-"))
        received = row[1] if isinstance(row[1], datetime) else datetime.fromisoformat(str(row[1])[:26])
    except ValueError:
        return None
    age = (datetime.now(timezone.utc).replace(tzinfo=None) - received).total_seconds()
    if age > 600:                      # not live data - the comparison would be meaningless
        return None
    return (sensor - (now - timedelta(seconds=age))).total_seconds()


def live_minutes(repo, now: datetime, hours: int) -> dict:
    """Per-minute (total, NG) for the last `hours` hours (sensor clock), cached for a few seconds."""
    key = (id(repo), now.strftime("%Y-%m-%d %H"), hours)
    hit = _live_cache.get(key)
    if hit and time.monotonic() - hit[0] < LIVE_CACHE_S:
        return hit[1]
    first = (now - timedelta(hours=hours - 1)).strftime("%Y-%m-%d %H")
    out: dict = {}
    with repo.engine.connect() as c:
        for hour, minute, n, ng in c.execute(text(
                "SELECT substr(timestamp, 1, 13), CAST(substr(timestamp, 15, 2) AS INTEGER), COUNT(*), "
                "SUM(CASE WHEN analysis_status = 'FAIL' THEN 1 ELSE 0 END) FROM inspection "
                "WHERE timestamp >= :a AND timestamp < :b GROUP BY 1, 2"),
                {"a": first, "b": (now + timedelta(days=1)).strftime("%Y-%m-%d")}):
            if hour and minute is not None:
                out.setdefault(hour, {})[minute] = (n, ng or 0)
    _live_cache.clear()
    _live_cache[key] = (time.monotonic(), out)
    return out


def collect(repo, s: Settings, hours: int, pid: str | None, now: datetime | None = None,
            live_hours: int = 3) -> Snapshot:
    now = now or datetime.now()
    snap = Snapshot(now=now, pid=pid, health=_read_health(s), chart_hours=hours, live_hours=live_hours)
    today = now.strftime("%Y-%m-%d")
    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    cur_hour = now.strftime("%Y-%m-%d %H")
    first_hour = (now - timedelta(hours=hours - 1)).strftime("%Y-%m-%d %H")
    try:
        snap.today = summarize(repo, today, tomorrow, by="day", per_sensor=True)
        snap.this_hour = summarize(repo, cur_hour, tomorrow, by="hour", per_sensor=True)
        snap.hours = summarize(repo, first_hour, tomorrow, by="hour")
        snap.tools = tool_summary(repo, today, tomorrow, per_sensor=True)
        if live_hours > 0:
            snap.live = live_minutes(repo, now, live_hours)
        since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=60)
        with repo.engine.connect() as c:
            snap.last_minute = {k or "-": n for k, n in c.execute(text(
                "SELECT camera_id, COUNT(*) FROM inspection WHERE created_at >= :t GROUP BY camera_id"),
                {"t": since})}
            snap.latest_ng = list(c.execute(text(
                "SELECT timestamp, camera_id, inspection_id, analysis_reason FROM inspection "
                "WHERE analysis_status = 'FAIL' ORDER BY id DESC LIMIT 5")))
            newest = c.execute(text("SELECT timestamp, created_at FROM inspection ORDER BY id DESC LIMIT 1")).first()
            snap.clock_offset_s = _clock_offset(newest, now)
            if not snap.today:
                snap.latest_hour = c.execute(text("SELECT MAX(hour) FROM hourly_stats")).scalar()
    except Exception as exc:  # noqa: BLE001 - keep the screen alive while the DB is busy/locked
        snap.db_error = str(exc).splitlines()[0][:150]
    return snap


# ------------------------------------------------------------------
# render
# ------------------------------------------------------------------

def _n(v) -> str:
    return "-" if v is None else f"{v:,}"


def _pct(v) -> str:
    return "-" if v is None else f"{v:.2f}%"


def _dur(seconds: float) -> str:
    seconds = int(max(seconds, 0))
    if seconds < 60:
        return f"{seconds}s"
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)
    return f"{d}d {h}h" if d else f"{h}h {m:02d}m" if h else f"{m}m"


def _age(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds()
    except ValueError:
        return None


def _sum(rows: list[PeriodStats]) -> PeriodStats | None:
    if not rows:
        return None
    tot = sum(r.total for r in rows)
    ok = sum(r.pass_count for r in rows)
    ng = sum(r.fail_count for r in rows)
    times = [(r.avg_time_ms, r.total) for r in rows if r.avg_time_ms is not None]
    known = [r.missing for r in rows if r.missing is not None]
    miss = sum(known) if known else None
    return PeriodStats(
        period="", sensor_id="ALL", total=tot, pass_count=ok, fail_count=ng,
        unknown_count=sum(r.unknown_count for r in rows),
        yield_pct=round(100 * ok / tot, 3) if tot else None, ng_pct=round(100 * ng / tot, 3) if tot else None,
        avg_time_ms=round(sum(a * n for a, n in times) / sum(n for _, n in times), 2) if times else None,
        max_time_ms=max((r.max_time_ms for r in rows if r.max_time_ms is not None), default=None),
        expected=None, missing=miss, active_hours=max(r.active_hours for r in rows),
        unknown_hours=sum(r.unknown_hours for r in rows),
    )


def render(snap: Snapshot, st: Style, width: int = 80, interval: float | None = None) -> list[str]:
    w = max(min(width, 120), 60)
    out: list[str] = []
    add = out.append
    alerts: list[str] = []
    h = snap.health or {}

    from app.version import read_version
    head = f" IV4 Data Agent {read_version()}  -  live monitor"
    clock = snap.now.strftime("%Y-%m-%d %H:%M:%S")
    hint = f"refresh {interval:g}s  Ctrl+C quit" if interval else ""
    add(st.bold(head) + " " * max(w - len(head) - len(clock) - 1, 1) + clock)
    add(st.dim("-" * w))

    # --- agent -------------------------------------------------------
    beat = _age(h.get("heartbeat_at"))
    if snap.pid:
        stale = beat is not None and beat > HEARTBEAT_STALE_S
        state = st.yellow("● STALLED") if stale else st.green("● RUNNING")
        up = _age(h.get("started_at"))
        line = f" Agent    {state}  pid {snap.pid}"
        if beat is not None:
            line += f"   heartbeat {beat:.0f}s ago"
        if up is not None:
            line += f"   up {_dur(up)}"
        if stale:
            alerts.append(f"agent heartbeat is {beat:.0f}s old - it may be stuck (see logs/agent.log)")
    else:
        line = f" Agent    {st.red('● STOPPED')}" + (f"   last heartbeat {_dur(beat)} ago" if beat is not None else "")
        alerts.append("agent is not running - start it with: run   (or the Windows service)")
    add(line)

    if snap.pid and h:
        lm = snap.last_minute
        speed = f"{sum(lm.values()) / 60:,.1f} /s last min"
        if len(lm) > 1:
            speed += "  (" + ", ".join(f"{k} {v / 60:.1f}" for k, v in sorted(lm.items())) + ")"
        backlog = {k: v for k, v in (h.get("incoming_by_sensor") or {}).items() if v or not k.startswith("incoming/")}
        wait = h.get("incoming_files", 0)
        wait_s = ", ".join(f"{k} {v}" for k, v in backlog.items()) or "0"
        add(f" Speed       {st.bold(speed)}{' ' * max(36 - len(speed), 2)}Waiting  " + (st.yellow(wait_s) if wait > BACKLOG_WARN else wait_s))
        if wait > BACKLOG_WARN:
            alerts.append(f"{wait:,} files waiting in incoming - the agent is falling behind")
        res = (f" Session  processed {_n(h.get('processed'))}   PASS {_n(h.get('pass'))}   "
               f"FAIL {_n(h.get('fail'))}   UNKNOWN {_n(h.get('unknown'))}   dup {_n(h.get('duplicates'))}")
        add(res)
    if h:
        errs = h.get("errors", 0)
        disk = h.get("disk_free_gb")
        low = disk is not None and disk < h.get("min_free_gb", 0)
        parts = [" Errors   " + (st.red(f"{errs:,}") if errs else st.green("0"))]
        if disk is not None:
            parts.append("Disk free " + (st.red(f"{disk:,.0f} GB LOW") if low else f"{disk:,.0f} GB"))
        up = h.get("upload") or {}
        if up.get("enabled"):
            pend = up.get("pending", 0)
            only = "all" if up.get("statuses") is None else "+".join(up["statuses"])
            parts.append(f"Upload {up.get('backend')} ({only}): {_n(up.get('uploaded', 0))} sent, {_n(pend)} pending"
                         + (st.red(" ERR") if up.get("last_upload_error") else ""))
            if up.get("last_upload_error"):
                alerts.append(f"upload: {up['last_upload_error'][:w - 20]}   (run upload = diagnosis)")
        else:
            parts.append("Upload " + st.yellow("OFF") + " (IV4_UPLOAD_ENABLED=false)")
        add("     ".join(parts))
        sh = h.get("sheets") or {}
        if sh.get("enabled"):
            beat_s = _age(sh.get("last_update_at"))
            line = " Sheets   " + (f"updated {_dur(beat_s)} ago" if beat_s is not None else "waiting for first update")
            if sh.get("last_error"):
                line += "  " + st.red("ERR")
                alerts.append(f"sheets: {sh['last_error'][:w - 20]}")
            add(line)
        if low:
            alerts.append("disk space low - oldest OK images are being deleted")
        err_age = _age(h.get("last_error_at"))
        if h.get("last_error") and err_age is not None and err_age < 3600:
            alerts.append(f"{_dur(err_age)} ago: {h['last_error'][:w - 20]}")

    off = snap.clock_offset_s
    if snap.pid and off is not None and abs(off) > 120:
        m = int(abs(off) // 60)
        alerts.append(f"sensor clock is {'ahead of' if off > 0 else 'behind'} this PC by {m // 60}h {m % 60:02d}m - "
                      "numbers use the SENSOR clock - fix the sensor clock, or set IV4_TIME_OFFSET_HOURS in .env")

    # --- today ---------------------------------------------------------
    add("")
    add(st.bold(f" TODAY {snap.now:%Y-%m-%d}") + st.dim("  (sensor clock)"))
    if snap.db_error:
        add("  " + st.yellow(f"database busy/unreadable: {snap.db_error}"))
    elif not snap.today:
        add("  no inspections yet today" + (f" - latest data is from {snap.latest_hour}:00 (check the sensor clock?)"
                                           if snap.latest_hour else ""))
    else:
        add(st.dim(f"  {'Sensor':<10}{'Total':>11}{'NG':>9}{'NG %':>9}{'Yield':>9}{'Missing':>9}{'Avg ms':>8}{'Run h':>7}"))
        rows = list(snap.today)
        if len(rows) > 1:
            rows.append(_sum(rows))
        for r in rows:
            name = r.sensor_id or "-"
            miss = miss_text(r.missing, r.unknown_hours)
            ng = f"{r.fail_count:,}"
            line = (f"  {name:<10}{r.total:>11,}{(st.red(f'{ng:>9}') if r.fail_count else f'{ng:>9}')}"
                    f"{_pct(r.ng_pct):>9}{_pct(r.yield_pct):>9}"
                    f"{(st.yellow(f'{miss:>9}') if r.missing else f'{miss:>9}')}"
                    f"{('-' if r.avg_time_ms is None else f'{r.avg_time_ms:.1f}'):>8}{r.active_hours:>7}")
            add(st.bold(line) if name == "ALL" else line)
        if any(r.unknown_hours or r.missing is None for r in rows):
            add(st.dim("  Missing: '*' = plus hours where the sensor counter restarted (not measurable); "
                       "'-' = no measurable hour yet"))
        for r in snap.this_hour:
            if r.missing:
                alerts.append(f"{r.sensor_id}: {r.missing:,} inspections missing this hour (sensor counted, file "
                              "never arrived) - check FTP")

    # --- hourly chart ---------------------------------------------------
    if snap.hours or not snap.db_error:
        add("")
        add(st.bold(f" LAST {len(_hour_slots(snap))} HOURS") + st.dim("  (all sensors)"))
        by_hour = {r.period: r for r in snap.hours}
        peak = max((r.total for r in snap.hours), default=0)
        bar_w = max(w - 46, 10)
        for slot in _hour_slots(snap):
            r = by_hour.get(slot)
            tot = r.total if r else 0
            n = round(bar_w * tot / peak) if peak else 0
            bar = st.cyan(BAR * n) + " " * (bar_w - n)
            ng = f"NG {r.fail_count:>6,} {_pct(r.ng_pct):>7}" if r else st.dim("-")
            add(f"  {slot[11:13]}:00 {bar} {tot:>9,}  {ng}")

    # --- live per-minute chart --------------------------------------------
    if snap.live_hours > 0 and not snap.db_error:
        add("")
        add(st.bold(" LIVE") + st.dim("  one cell = one minute (sensor clock), height = inspections, red = has NG"))
        peak = max((n for mins in snap.live.values() for n, _ in mins.values()), default=0)
        for i in range(snap.live_hours - 1, -1, -1):
            hr = snap.now - timedelta(hours=i)
            key = hr.strftime("%Y-%m-%d %H")
            mins = snap.live.get(key, {})
            current = i == 0
            cells = []
            for m in range(60):
                if current and m > snap.now.minute:
                    cells.append(" ")
                    continue
                n, ng = mins.get(m, (0, 0))
                if n == 0:
                    cells.append(st.dim("·"))
                    continue
                ch = SPARK[min(len(SPARK) - 1, (n * len(SPARK) - 1) // peak)] if peak else SPARK[0]
                cells.append(st.red(ch) if ng else st.cyan(ch))
            tot = sum(n for n, _ in mins.values())
            tng = sum(g for _, g in mins.values())
            add(f"  {hr:%H}:00 {''.join(cells)} {tot:>8,}  NG {tng:>6,}")
        add(st.dim(f"        00{' ' * 13}15{' ' * 13}30{' ' * 13}45{' ' * 13}min  (tallest cell = {peak:,}/min)"))

    # --- tools ---------------------------------------------------------
    if snap.tools:
        add("")
        add(st.bold(" TOOLS TODAY") + st.dim("  (NG count, lowest value that still passed = margin to NG)"))
        for t in sorted(snap.tools, key=lambda t: (-t.ng_count, t.sensor_id or "", t.tool_no))[:6]:
            name = f"Tool{t.tool_no:02d} {t.tool_name or ''}".strip()[:24]
            ng = f"NG {t.ng_count:>7,} {_pct(t.ng_pct):>7}"
            add(f"  {t.sensor_id or '-':<10}{name:<25}{(st.red(ng) if t.ng_count else ng)}"
                f"   avg {('-' if t.avg_value is None else f'{t.avg_value:.1f}'):>6}"
                f"   min OK {('-' if t.min_ok_value is None else f'{t.min_ok_value:g}'):>5}")

    # --- latest NG -------------------------------------------------------
    add("")
    add(st.bold(" LATEST NG"))
    if not snap.latest_ng:
        add(st.dim("  none"))
    for ts, sensor, iid, reason in snap.latest_ng:
        why = "; ".join(p for p in (reason or "").split("; ") if not p.startswith("Inspection result is"))
        line = f"  {(ts or '')[:19]:<20}{sensor or '-':<10}{iid:<24}{why}"
        add(line[: w + 2])

    # --- alerts ---------------------------------------------------------
    add("")
    if alerts:
        add(st.bold(" ALERTS"))
        for a in alerts:
            add("  " + st.yellow("! " + a))
    else:
        add(" " + st.green("OK - no alerts"))
    if hint:
        add(st.dim(" " + hint))
    return out


def _hour_slots(snap: Snapshot) -> list[str]:
    n = snap.chart_hours
    return [(snap.now - timedelta(hours=i)).strftime("%Y-%m-%d %H") for i in range(n - 1, -1, -1)]


# ------------------------------------------------------------------
# loop
# ------------------------------------------------------------------

def run(args: list[str], s: Settings, running_pid) -> int:
    ap = argparse.ArgumentParser(prog="run monitor", description="Live terminal monitor (Ctrl+C to quit).")
    ap.add_argument("--interval", type=float, default=2.0, help="seconds between refreshes (default 2)")
    ap.add_argument("--hours", type=int, default=8, help="hours in the hourly chart (default 8)")
    ap.add_argument("--live-hours", type=int, default=3, help="rows in the per-minute live chart (default 3, 0 = hide)")
    ap.add_argument("--once", action="store_true", help="print one snapshot and exit")
    ap.add_argument("--no-color", action="store_true")
    a = ap.parse_args(args)
    a.interval = max(a.interval, 0.5)
    a.hours = min(max(a.hours, 1), 48)
    a.live_hours = min(max(a.live_hours, 0), 12)

    from app.database.repository import DatabaseRepository

    repo = DatabaseRepository(s.database_path)
    live = not a.once and sys.stdout.isatty()
    st = Style(_enable_ansi() and not a.no_color)

    def frame() -> list[str]:
        snap = collect(repo, s, a.hours, running_pid(), live_hours=a.live_hours)
        width = shutil.get_terminal_size((100, 40)).columns
        return render(snap, st, width, a.interval if live else None)

    try:
        if not live:
            print("\n".join(frame()))
            return 0
        sys.stdout.write("\033[?1049h\033[?25l")       # alternate screen, hide cursor
        while True:
            lines = frame()
            sys.stdout.write("\033[H" + "".join(f"{ln}\033[K\n" for ln in lines) + "\033[J")
            sys.stdout.flush()
            time.sleep(a.interval)
    except KeyboardInterrupt:
        return 0
    finally:
        if live:
            sys.stdout.write("\033[?25h\033[?1049l")
            sys.stdout.flush()
        repo.dispose()
