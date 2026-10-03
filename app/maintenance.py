"""
Housekeeping that runs in its own thread (never blocks ingestion):

* Retention: delete archive/<YYYY-MM-DD>/<OK|NG|UNKNOWN>/ folders older
  than IV4_RETENTION_OK_DAYS / IV4_RETENTION_NG_DAYS (0 = keep forever).
* Optional: delete per-inspection DB rows older than
  IV4_RETENTION_ROWS_DAYS. Hourly statistics are always kept, so metrics
  and dashboards for old periods still work.
* Disk space check (also reported in health.json).

Only folders whose name is a date under archive/ are ever deleted.
"""
from __future__ import annotations

import logging
import re
import shutil
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from app.config import Settings
from app.database.repository import DatabaseRepository

log = logging.getLogger("iv4.maintenance")

_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
NG_FOLDERS = ("NG", "UNKNOWN")


def disk_free_gb(path: Path) -> float | None:
    try:
        return round(shutil.disk_usage(path).free / 1024**3, 1)
    except OSError:
        return None


def purge_archive(archive_dir: Path, ok_days: int, ng_days: int, today: date | None = None) -> list[str]:
    """Delete expired day/status folders. Returns the relative paths removed."""
    today = today or date.today()
    removed: list[str] = []
    if not archive_dir.exists():
        return removed

    for day_dir in sorted(p for p in archive_dir.iterdir() if p.is_dir() and _DAY_RE.match(p.name)):
        try:
            day = date.fromisoformat(day_dir.name)
        except ValueError:
            continue
        age = (today - day).days
        for sub in sorted(p for p in day_dir.iterdir() if p.is_dir()):
            keep = ok_days if sub.name == "OK" else ng_days if sub.name in NG_FOLDERS else 0
            if keep > 0 and age >= keep:
                shutil.rmtree(sub, ignore_errors=True)
                removed.append(f"{day_dir.name}/{sub.name}")
        if not any(day_dir.iterdir()):
            day_dir.rmdir()
    return removed


class MaintenanceWorker(threading.Thread):

    def __init__(self, settings: Settings, stop_event: threading.Event):
        super().__init__(name="maintenance", daemon=True)
        self.settings = settings
        self.stop_event = stop_event
        self.stats: dict = {"last_run_at": None, "removed_folders": 0, "removed_rows": 0, "last_error": None}

    def run_once(self, repo: DatabaseRepository) -> None:
        s = self.settings
        removed = purge_archive(s.archive_dir, s.retention_ok_days, s.retention_ng_days)
        for r in removed:
            log.info("[RETENTION] deleted images %s", r)
        self.stats["removed_folders"] += len(removed)

        if s.retention_rows_days > 0:
            cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=s.retention_rows_days)
            n = repo.delete_rows_before(cutoff)
            if n:
                log.info("[RETENTION] deleted %d inspection rows older than %s", n, cutoff.date())
            self.stats["removed_rows"] += n

        free = disk_free_gb(s.archive_dir)
        if free is not None and free < s.min_free_gb:
            log.error("[DISK] only %.1f GB free on %s (minimum %.0f GB) - set retention or add storage",
                      free, s.archive_dir, s.min_free_gb)
        self.stats["last_run_at"] = datetime.now(timezone.utc).isoformat()

    def run(self) -> None:
        repo = DatabaseRepository(self.settings.database_path)
        try:
            while not self.stop_event.is_set():
                try:
                    self.run_once(repo)
                    self.stats["last_error"] = None
                except Exception as exc:  # noqa: BLE001
                    log.exception("[MAINTENANCE] failed")
                    self.stats["last_error"] = str(exc)[:300]
                self.stop_event.wait(self.settings.cleanup_interval)
        finally:
            repo.dispose()
