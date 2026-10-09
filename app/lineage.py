"""
Lineage: which program version and which settings produced each inspection result.

At start the agent takes a snapshot of its settings (secrets and machine paths removed), gives it a short hash and
stores it once in `config_history`. Every inspection row then carries `app_version` + `config_hash`, so a change in
NG% can be traced to "version 1.8.0 with IV4_DRIFT_PCT=10" instead of guessing. `run lineage` shows the history.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from pathlib import Path

SECRET = re.compile(r"token|secret|password|key|chat_id|credential", re.I)


def snapshot(settings) -> dict:
    """The settings that shape results: no secrets, no machine paths."""
    out = {}
    for f in dataclasses.fields(settings):
        v = getattr(settings, f.name)
        if SECRET.search(f.name) or isinstance(v, Path):
            continue
        out[f.name] = v if isinstance(v, (int, float, str, bool, type(None))) else str(v)
    return dict(sorted(out.items()))


def config_hash(snap: dict) -> str:
    return hashlib.sha256(json.dumps(snap, sort_keys=True, default=str).encode()).hexdigest()[:10]


def run(args: list[str], s, _running_pid=None) -> int:
    """`run lineage`: every settings snapshot the agent has run with, and how many inspections each produced."""
    from sqlalchemy import text

    from app.database.repository import DatabaseRepository
    repo = DatabaseRepository(s.database_path)
    try:
        with repo.engine.connect() as c:
            rows = list(c.execute(text(
                "SELECT h.hash, h.app_version, h.first_seen, h.settings_json,"
                " (SELECT COUNT(*) FROM inspection i WHERE i.config_hash = h.hash) FROM config_history h"
                " ORDER BY h.first_seen")))
    finally:
        repo.dispose()
    if not rows:
        print("No history yet - it is recorded when the agent starts (version 1.9.0+).")
        return 0
    prev = None
    for h, ver, seen, js, n in rows:
        cur = json.loads(js)
        diff = "" if prev is None else "  changed: " + (", ".join(
            f"{k}={cur.get(k)!r}" for k in sorted(cur) if prev.get(k) != cur.get(k)) or "(none)")
        print(f"{seen:%Y-%m-%d %H:%M}  v{ver}  config {h}  {n:,} inspections{diff}")
        prev = cur
    return 0
