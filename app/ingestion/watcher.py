"""
IV4 Data Agent – realtime ingestion service.

Design (production):
  * watchdog events only *wake up* the scanner; the scanner is the source
    of truth. Missed events (Windows buffer overflow, network share, agent
    restart) are therefore harmless: the next scan picks the files up.
  * Stability is tracked across scans (no sleeping inside callbacks).
  * Each physical inspection gets a unique ``uid`` (filename id + arrival
    time) so a reset IV4 counter never overwrites or blocks older data.
  * Anything that cannot be processed is moved to data/error/<name>/ with
    ERROR.txt – nothing is deleted, nothing gets stuck in incoming/.
  * Database errors are retried (folder stays in processing/) instead of
    quarantining good data.
  * On start-up, half-processed folders in processing/ are recovered.
"""
from __future__ import annotations

import json
import logging
import shutil
import signal
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.polling import PollingObserver

from app.config import Settings, load_settings
from app.database.repository import DatabaseRepository
from app.ingestion.lifecycle import move_files, quarantine, unique_dir
from app.ingestion.matcher import (
    COMPLETE,
    INVALID,
    WAITING,
    GroupStatus,
    find_groups,
    validate_group,
)
from app.ingestion.record import (
    CLAIMED,
    DONE,
    FAILED,
    content_hash,
    create_manifest,
    load_manifest,
    make_uid,
    save_manifest,
    utc_now,
)
from app.ingestion.stability import StabilityTracker
from app.maintenance import MaintenanceWorker, disk_free_gb
from app.pipeline import PipelineError, archive_rel_path, run_pipeline

log = logging.getLogger("iv4.agent")

DB_RETRY_INTERVAL = 30.0
DB_MAX_ATTEMPTS = 20


class _WakeHandler(FileSystemEventHandler):
    def __init__(self, wake: threading.Event):
        super().__init__()
        self.wake = wake

    def on_any_event(self, event):
        if not event.is_directory:
            self.wake.set()


class IV4Agent:

    def __init__(
        self,
        settings: Settings,
        repository: DatabaseRepository | None = None,
        clock=time.monotonic,
    ):
        self.settings = settings
        settings.ensure_dirs()
        self.repo = repository or DatabaseRepository(settings.database_path)
        self.tracker = StabilityTracker(settings.settle_seconds, clock=clock)
        self.clock = clock

        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self.upload_worker = None
        self.maintenance = None
        self._last_retry = 0.0

        self.stats = {
            "started_at": utc_now(),
            "processed": 0,
            "pass": 0,
            "fail": 0,
            "unknown": 0,
            "duplicates": 0,
            "errors": 0,
            "last_success_at": None,
            "last_error_at": None,
            "last_error": None,
        }

    # ------------------------------------------------------------
    # Scan incoming/
    # ------------------------------------------------------------

    def scan_once(self) -> None:
        """One pass over incoming/ and every sensor subfolder incoming/<sensor>/."""
        s = self.settings
        sources = s.sensor_sources()
        found: list[tuple[str, Path, str, list[Path]]] = []
        for sensor, folder in sources:
            groups = find_groups(folder, s.supported_image_ext, s.supported_text_ext)
            found += [(sensor, folder, gid, files) for gid, files in groups.items()]

        self.tracker.prune({f for *_, files in found for f in files})
        now = self.clock()
        root = s.incoming_dir

        for sensor, folder, group_id, files in sorted(found, key=lambda x: (x[0], x[2])):
            if self.stop_event.is_set():
                return

            stable = [self.tracker.observe(f) for f in files]
            all_stable = all(stable)

            status = validate_group(
                group_id, files, s.supported_image_ext, s.supported_text_ext,
                s.expected_images, s.expected_texts,
            )
            status.sensor_id = sensor
            status.source_dir = folder
            status.uid_prefix = None if folder == root else sensor

            if status.status == COMPLETE and all_stable:
                self._handle_complete(status)

            elif status.status == INVALID and all_stable:
                self._quarantine_loose(status, f"INVALID group: {status.reason}")

            elif status.status == WAITING:
                first = min(
                    (self.tracker.first_seen(f) or now) for f in files
                )
                if now - first >= s.group_timeout and all_stable:
                    names = ", ".join(f.name for f in status.files)
                    self._quarantine_loose(
                        status,
                        f"INCOMPLETE after {s.group_timeout:.0f}s "
                        f"(images={len(status.image_files)}, texts={len(status.text_files)}): {names}",
                    )

    def _quarantine_loose(self, status: GroupStatus, reason: str) -> None:
        name = make_uid(status.group_id, sensor=status.uid_prefix)
        quarantine(status.files, self.settings.error_dir, name, reason)
        self.tracker.forget(status.files)
        self._record_error(reason)

    # ------------------------------------------------------------
    # Claim + process one complete group
    # ------------------------------------------------------------

    def _handle_complete(self, status: GroupStatus) -> None:
        s = self.settings
        group_id = status.group_id
        uid = make_uid(group_id, sensor=status.uid_prefix)
        folder = s.processing_dir / uid

        moved: list[Path] = []
        try:
            sha = content_hash(status.files)
            for f in status.files:
                moved += move_files([f], folder)
        except OSError as exc:
            # e.g. IV4 still has the file open on Windows -> roll back, retry next scan
            log.warning("[CLAIM] group=%s not movable yet: %s", group_id, exc)
            for m in moved:
                try:
                    move_files([m], status.source_dir)
                except OSError:
                    log.exception("[CLAIM] rollback failed for %s", m)
            try:
                folder.rmdir()
            except OSError:
                pass
            return
        finally:
            self.tracker.forget(status.files)

        image = next((f for f in moved if f.suffix.lower() in s.supported_image_ext), None)
        texts = [f for f in moved if f.suffix.lower() in s.supported_text_ext]
        manifest = create_manifest(uid, group_id, image or folder, texts, sha, sensor_id=status.sensor_id)
        save_manifest(manifest, folder)
        log.info("[CLAIMED] sensor=%s group=%s uid=%s files=%d", status.sensor_id, group_id, uid, len(moved))

        self._process_folder(folder, manifest)

    def _process_folder(self, folder: Path, manifest: dict) -> None:
        uid = manifest["uid"]
        manifest["attempts"] = manifest.get("attempts", 0) + 1
        try:
            result = run_pipeline(folder, manifest, self.repo, self.settings)

        except PipelineError as exc:
            manifest["error"] = str(exc)
            if exc.stage == "DATABASE" and manifest["attempts"] < DB_MAX_ATTEMPTS:
                save_manifest(manifest, folder)
                log.error("[DB RETRY] uid=%s attempt=%d %s", uid, manifest["attempts"], exc)
                self._record_error(str(exc))
                return
            manifest["status"] = FAILED
            save_manifest(manifest, folder)
            quarantine(folder, self.settings.error_dir, uid, str(exc))
            self._record_error(str(exc))
            return

        except Exception as exc:  # noqa: BLE001 – never let one file kill the service
            log.exception("[UNEXPECTED] uid=%s", uid)
            manifest["status"] = FAILED
            manifest["error"] = repr(exc)
            save_manifest(manifest, folder)
            quarantine(folder, self.settings.error_dir, uid, f"UNEXPECTED: {exc!r}")
            self._record_error(repr(exc))
            return

        if result.action == "DUPLICATE":
            manifest["status"] = FAILED
            manifest["error"] = f"DUPLICATE of database id {result.database_id}"
            save_manifest(manifest, folder)
            quarantine(folder, self.settings.error_dir, uid, manifest["error"])
            self.stats["duplicates"] += 1
            return

        manifest["status"] = DONE
        manifest["error"] = None
        manifest["analysis_status"] = result.status
        manifest["database_id"] = result.database_id
        manifest["archive"] = result.archive_rel
        save_manifest(manifest, folder)
        self._archive(folder, manifest)

        self.stats["processed"] += 1
        key = result.status.lower()
        if key in self.stats:
            self.stats[key] += 1
        self.stats["last_success_at"] = utc_now()

        log.info(
            "[DONE] uid=%s status=%s action=%s db_id=%s",
            uid, result.status, result.action, result.database_id,
        )

    # ------------------------------------------------------------
    # Recovery: processing/ folders left CLAIMED (crash / DB down)
    # ------------------------------------------------------------

    def _archive(self, folder: Path, manifest: dict) -> None:
        """processing/<uid>/ -> archive/<date>/<OK|NG|UNKNOWN>/<uid>/ (keeps processing/ small)."""
        rel = manifest.get("archive")
        if not rel:
            rel = archive_rel_path(
                manifest["uid"], manifest.get("analysis_status", "UNKNOWN"), None, manifest.get("created_at")
            )
            manifest["archive"] = rel
            save_manifest(manifest, folder)
            self.repo.set_folder(manifest["uid"], rel)
        target = self.settings.archive_dir / rel
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                target = unique_dir(target.parent, target.name)
                rel = str(target.relative_to(self.settings.archive_dir)).replace("\\", "/")
                self.repo.set_folder(manifest["uid"], rel)
            shutil.move(str(folder), str(target))
        except OSError as exc:
            log.warning("[ARCHIVE] uid=%s will retry: %s", manifest.get("uid"), exc)

    def recover_processing(self) -> int:
        """Finish anything left in processing/ (crash, DB down, archive move failed)."""
        recovered = 0
        for folder in sorted(p for p in self.settings.processing_dir.iterdir() if p.is_dir()):
            manifest = load_manifest(folder)
            if manifest is None:
                continue  # legacy v1 folder / broken manifest: leave untouched
            status = manifest.get("status")
            if status == CLAIMED:
                log.info("[RECOVER] uid=%s", manifest.get("uid"))
                self._process_folder(folder, manifest)
                recovered += 1
            elif status == DONE:
                self._archive(folder, manifest)
        return recovered

    # ------------------------------------------------------------
    # Health
    # ------------------------------------------------------------

    def _record_error(self, msg: str) -> None:
        self.stats["errors"] += 1
        self.stats["last_error_at"] = utc_now()
        self.stats["last_error"] = msg[:500]

    def write_health(self) -> None:
        per_sensor = {}
        for sensor, folder in self.settings.sensor_sources():
            key = "incoming/ (root)" if folder == self.settings.incoming_dir else sensor
            try:
                per_sensor[key] = sum(1 for p in folder.iterdir() if p.is_file())
            except OSError:
                per_sensor[key] = -1
        pending = sum(v for v in per_sensor.values() if v > 0)
        health = {
            **self.stats,
            "heartbeat_at": utc_now(),
            "incoming_files": pending,
            "incoming_by_sensor": per_sensor,
            "incoming_dir": str(self.settings.incoming_dir),
            "database": str(self.settings.database_path),
            "disk_free_gb": disk_free_gb(self.settings.archive_dir),
            "min_free_gb": self.settings.min_free_gb,
            "archive_dir": str(self.settings.archive_dir),
            "maintenance": self.maintenance.stats if self.maintenance else None,
            "upload": (
                {"enabled": True, "backend": self.settings.upload_backend, **self.upload_worker.stats}
                if self.upload_worker else {"enabled": False}
            ),
        }
        path = self.settings.log_dir / "health.json"
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(health, indent=2), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            log.warning("[HEALTH] cannot write %s: %s", path, exc)

    # ------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------

    def run(self) -> None:
        s = self.settings
        log.info("=" * 60)
        log.info("IV4 Data Agent starting")
        log.info("Incoming   : %s", s.incoming_dir)
        for sensor, folder in s.sensor_sources():
            log.info("  sensor %-10s <- %s", sensor, folder)
        log.info("Processing : %s", s.processing_dir)
        log.info("Error      : %s", s.error_dir)
        log.info("Database   : %s", s.database_path)
        log.info("Settle     : %.1fs  Group timeout: %.0fs", s.settle_seconds, s.group_timeout)
        log.info("Archive    : %s", s.archive_dir)
        log.info("Retention  : OK=%s NG=%s rows=%s (days, 0=keep)",
                 s.retention_ok_days, s.retention_ng_days, s.retention_rows_days)
        log.info("Upload     : %s", (
            f"{s.upload_backend} ({'ALL' if s.upload_statuses is None else ','.join(sorted(s.upload_statuses))})"
            if s.upload_enabled else "disabled"))
        log.info("=" * 60)

        n = self.recover_processing()
        if n:
            log.info("[RECOVER] %d inspection(s) re-processed", n)

        observer = self._start_observer()

        self.maintenance = MaintenanceWorker(s, self.stop_event)
        self.maintenance.start()

        if s.upload_enabled:
            from app.upload.queue import UploadWorker

            self.upload_worker = UploadWorker(s, self.stop_event)
            self.upload_worker.start()

        try:
            while not self.stop_event.is_set():
                try:
                    self.scan_once()
                    if self.clock() - self._last_retry >= DB_RETRY_INTERVAL:
                        self._last_retry = self.clock()
                        self.recover_processing()
                    self.write_health()
                except Exception:  # noqa: BLE001
                    log.exception("[SCAN] unexpected error; continuing")
                self.wake.wait(s.scan_interval)
                self.wake.clear()
        finally:
            if observer is not None:
                observer.stop()
                observer.join(timeout=5)
            if self.upload_worker is not None:
                self.upload_worker.join(timeout=30)
            if self.maintenance is not None:
                self.maintenance.join(timeout=30)
            self.write_health()
            self.repo.dispose()
            log.info("IV4 Data Agent stopped")

    def _start_observer(self):
        cls = PollingObserver if self.settings.use_polling else Observer
        try:
            observer = cls()
            observer.schedule(_WakeHandler(self.wake), str(self.settings.incoming_dir), recursive=True)
            observer.start()
            return observer
        except Exception as exc:  # noqa: BLE001
            log.warning("[WATCH] file events unavailable (%s); polling every %.1fs only",
                        exc, self.settings.scan_interval)
            return None

    def stop(self, *_):
        self.stop_event.set()
        self.wake.set()


def main() -> None:
    from app.logging_setup import setup_logging

    settings = load_settings()
    setup_logging(settings)
    agent = IV4Agent(settings)

    signal.signal(signal.SIGINT, agent.stop)
    signal.signal(signal.SIGTERM, agent.stop)
    if hasattr(signal, "SIGBREAK"):  # Windows service stop / Ctrl+Break
        signal.signal(signal.SIGBREAK, agent.stop)

    agent.run()


if __name__ == "__main__":
    main()
