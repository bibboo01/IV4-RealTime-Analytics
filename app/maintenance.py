"""
Housekeeping that runs in its own thread (never blocks ingestion):

* Retention: delete archive/<YYYY-MM-DD>/<OK|NG|UNKNOWN>/ folders older
  than IV4_RETENTION_OK_DAYS / IV4_RETENTION_NG_DAYS (0 = keep forever).
* Optional: delete per-inspection DB rows older than
  IV4_RETENTION_ROWS_DAYS. Hourly statistics are always kept, so metrics
  and dashboards for old periods still work.
* Disk guard: when free space drops below IV4_MIN_FREE_GB, delete the
  OLDEST OK images one hour-folder at a time until there is headroom again
  (IV4_DISK_PRUNE_OK, default on). NG / UNKNOWN images are never deleted by
  the disk guard - only by IV4_RETENTION_NG_DAYS. This makes storage
  self-managing when production hours vary from day to day.

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


_HOUR_RE = re.compile(r"^\d{2}$")


def oldest_ok_hours(archive_dir: Path, protect: str | None = None) -> list[Path]:
    """
    OK image folders oldest first: archive/<day>/OK/<HH>. Folders from the
    old layout (archive/<day>/OK/<uid>) are returned per day. ``protect`` =
    'YYYY-MM-DD HH' that must not be touched (current hour).
    """
    out: list[Path] = []
    if not archive_dir.exists():
        return out
    for day_dir in sorted(p for p in archive_dir.iterdir() if p.is_dir() and _DAY_RE.match(p.name)):
        ok = day_dir / "OK"
        if not ok.is_dir():
            continue
        children = sorted(p for p in ok.iterdir() if p.is_dir())
        if children and not all(_HOUR_RE.match(c.name) for c in children):
            if not (protect and protect.startswith(day_dir.name)):
                out.append(ok)                 # legacy flat day folder
            continue
        for h in children:
            if protect != f"{day_dir.name} {h.name}":
                out.append(h)
    return out


def prune_for_space(archive_dir: Path, min_free_gb: float, target_free_gb: float,
                    free_fn=None, protect: str | None = None) -> list[str]:
    free_fn = free_fn or disk_free_gb
    removed: list[str] = []
    free = free_fn(archive_dir)
    if free is None or free >= min_free_gb:
        return removed
    for folder in oldest_ok_hours(archive_dir, protect=protect):
        shutil.rmtree(folder, ignore_errors=True)
        removed.append(str(folder.relative_to(archive_dir)).replace("\\", "/"))
        parent = folder.parent
        for d in (parent, parent.parent) if folder.name != "OK" else (parent,):
            try:
                d.rmdir()                      # only if now empty
            except OSError:
                pass
        free = free_fn(archive_dir)
        if free is None or free >= target_free_gb:
            break
    return removed


class MaintenanceWorker(threading.Thread):

    def __init__(self, settings: Settings, stop_event: threading.Event):
        super().__init__(name="maintenance", daemon=True)
        self.settings = settings
        self.stop_event = stop_event
        self.stats: dict = {
            "last_run_at": None, "removed_folders": 0, "removed_rows": 0,
            "disk_pruned_ok_hours": 0, "last_disk_prune": None, "last_error": None,
        }

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

        self.check_disk()
        self.stats["last_run_at"] = datetime.now(timezone.utc).isoformat()

    def check_disk(self) -> None:
        s = self.settings
        free = disk_free_gb(s.archive_dir)
        if free is None or free >= s.min_free_gb:
            return
        if s.disk_prune_ok:
            removed = prune_for_space(
                s.archive_dir, s.min_free_gb, s.min_free_gb * 1.25,
                protect=datetime.now().strftime("%Y-%m-%d %H"),
            )
            if removed:
                log.warning("[DISK] %.1f GB free < %.0f GB: deleted oldest OK images %s .. %s (%d hour folders)",
                            free, s.min_free_gb, removed[0], removed[-1], len(removed))
                self.stats["disk_pruned_ok_hours"] += len(removed)
                self.stats["last_disk_prune"] = {"at": datetime.now(timezone.utc).isoformat(),
                                                 "from": removed[0], "to": removed[-1]}
            free = disk_free_gb(s.archive_dir)
        if free is not None and free < s.min_free_gb:
            log.error("[DISK] only %.1f GB free on %s (minimum %.0f GB) and no OK images left to prune - "
                      "add storage or set IV4_RETENTION_NG_DAYS", free, s.archive_dir, s.min_free_gb)

    def run(self) -> None:
        import time

        repo = DatabaseRepository(self.settings.database_path)
        last_full = 0.0
        try:
            while not self.stop_event.is_set():
                try:
                    if time.monotonic() - last_full >= self.settings.cleanup_interval:
                        self.run_once(repo)          # retention + disk
                        last_full = time.monotonic()
                    else:
                        self.check_disk()            # disk only, more often
                    self.stats["last_error"] = None
                except Exception as exc:  # noqa: BLE001
                    log.exception("[MAINTENANCE] failed")
                    self.stats["last_error"] = str(exc)[:300]
                self.stop_event.wait(min(self.settings.disk_check_interval, self.settings.cleanup_interval))
        finally:
            repo.dispose()
