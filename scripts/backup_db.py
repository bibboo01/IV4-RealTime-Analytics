"""
Online SQLite backup (safe while the agent is running, WAL-aware).

    python -m scripts.backup_db                 # -> backups/iv4_YYYYmmdd_HHMMSS.db
    python -m scripts.backup_db --keep 30       # keep newest 30 backups

Schedule it daily with Windows Task Scheduler (see deploy/README_DEPLOY.md).
"""
from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path

from app.config import BASE_DIR, load_settings


def backup(dest_dir: Path, keep: int) -> Path:
    settings = load_settings()
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / f"iv4_{datetime.now():%Y%m%d_%H%M%S}.db"

    src = sqlite3.connect(settings.database_path)
    dst = sqlite3.connect(target)
    with dst:
        src.backup(dst)
    dst.close()
    src.close()

    ok = sqlite3.connect(target).execute("PRAGMA integrity_check").fetchone()[0]
    if ok != "ok":
        raise SystemExit(f"Backup integrity check failed: {ok}")

    backups = sorted(dest_dir.glob("iv4_*.db"))
    for old in backups[:-keep]:
        old.unlink()
    return target


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", default=str(BASE_DIR / "backups"))
    ap.add_argument("--keep", type=int, default=30)
    a = ap.parse_args()
    print(backup(Path(a.dest), a.keep))
