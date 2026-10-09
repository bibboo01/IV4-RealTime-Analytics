"""
`run heal` - restart the agent by itself when it is alive but stuck (heartbeat stops updating).

The 5-minute health-check task calls this, so a hang (locked database, frozen disk call) is fixed in minutes
without anyone watching. Safe by design:
  * only acts when the agent process exists but its heartbeat is older than IV4_HEAL_STALE_SEC (default 180 s)
    and it has been up longer than that (a fresh start is never "stuck");
  * never starts an agent you stopped on purpose (`run stop`);
  * at most 3 restarts per hour - if it keeps hanging, it stops and leaves the problem visible instead of looping;
  * nothing is lost: files wait in incoming/ and are processed after the restart.
Every decision is appended to logs/heal.log.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

MAX_PER_HOUR = 3


def _age(iso: str | None, now: datetime) -> float | None:
    try:
        return (now - datetime.fromisoformat(iso)).total_seconds()
    except (TypeError, ValueError):
        return None


def decide(health: dict | None, pid: str | None, now: datetime, history: list[datetime], stale_sec: int) -> tuple[str, str]:
    """('restart' | 'ok' | 'skip', reason)."""
    if not pid:
        return "skip", "agent is not running (not started here: it may have been stopped on purpose)"
    if not health:
        return "skip", "no health.json yet"
    beat, up = _age(health.get("heartbeat_at"), now), _age(health.get("started_at"), now)
    if beat is None:
        return "skip", "heartbeat unreadable"
    if beat <= stale_sec:
        return "ok", f"heartbeat {beat:.0f}s old"
    if up is not None and up <= stale_sec:
        return "skip", f"agent started only {up:.0f}s ago"
    recent = [t for t in history if now - t < timedelta(hours=1)]
    if len(recent) >= MAX_PER_HOUR:
        return "skip", f"heartbeat {beat:.0f}s old but already restarted {len(recent)}x in the last hour - needs a person"
    return "restart", f"heartbeat {beat:.0f}s old (limit {stale_sec}s)"


def _load(path) -> list[datetime]:
    try:
        return [datetime.fromisoformat(x) for x in json.loads(path.read_text(encoding="utf-8"))]
    except (OSError, ValueError, TypeError):
        return []


def run(args: list[str], s, running_pid, restart=None, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    state = s.log_dir / "heal.json"
    s.log_dir.mkdir(parents=True, exist_ok=True)
    try:
        health = json.loads((s.log_dir / "health.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        health = None
    history = _load(state)
    action, why = decide(health, running_pid(), now, history, s.heal_stale_sec)
    dry = "--dry-run" in args
    line = f"{now:%Y-%m-%d %H:%M:%S}Z {action}: {why}" + (" (dry run)" if dry and action == "restart" else "")
    print(line)
    if action != "ok":
        with open(s.log_dir / "heal.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    if action != "restart" or dry:
        return 0
    state.write_text(json.dumps([t.isoformat() for t in [*history, now][-20:]]), encoding="utf-8")
    if restart is None:
        from app.cli import cmd_restart
        restart = lambda: cmd_restart(["--force"], s)  # noqa: E731
    rc = restart()
    with open(s.log_dir / "heal.log", "a", encoding="utf-8") as f:
        f.write(f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}Z restart finished, exit code {rc}\n")
    return rc
