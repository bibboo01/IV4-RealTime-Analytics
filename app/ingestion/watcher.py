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
from concurrent.futures import ThreadPoolExecutor
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
    rebuild_manifest,
    save_manifest,
    utc_now,
)
from app.ingestion.stability import StabilityTracker
from app.maintenance import MaintenanceWorker, disk_free_gb
from app.pipeline import PipelineError, archive_rel_path, prepare
from app.version import read_version

log = logging.getLogger("iv4.agent")

STOP_FLAG = "stop.flag"        # created in log_dir by `run stop`

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
        self.sheets_worker = None
        self.notify_worker = None
        self.maintenance = None
        self.pool = ThreadPoolExecutor(max_workers=settings.worker_count(), thread_name_prefix="iv4-worker")
        self._last_retry = 0.0

        self.stats = {
            "version": read_version(),
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

        ready: list[GroupStatus] = []
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
                ready.append(status)

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

        bs = max(1, s.batch_size)
        for i in range(0, len(ready), bs):
            if self.stop_event.is_set():
                return
            chunk = ready[i:i + bs]
            claimed = [c for c in self.pool.map(self._claim, chunk) if c is not None]
            for st in chunk:
                self.tracker.forget(st.files)
            self._process_claimed(claimed)

    def _quarantine_loose(self, status: GroupStatus, reason: str) -> None:
        name = make_uid(status.group_id, sensor=status.uid_prefix)
        quarantine(status.files, self.settings.error_dir, name, reason)
        self.tracker.forget(status.files)
        self._record_error(reason)

    # ------------------------------------------------------------
    # Claim + process (parallel file work, one DB transaction per batch)
    # ------------------------------------------------------------

    def _claim(self, status: GroupStatus) -> tuple[Path, dict] | None:
        """Worker thread: move a complete group into processing/<uid>/ + write manifest."""
        s = self.settings
        group_id = status.group_id
        uid = make_uid(group_id, sensor=status.uid_prefix)
        folder = s.processing_dir / uid

        moved: list[Path] = []
        try:
            sha = content_hash(status.files, status.sensor_id)
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
            return None

        image = next((f for f in moved if f.suffix.lower() in s.supported_image_ext), None)
        texts = [f for f in moved if f.suffix.lower() in s.supported_text_ext]
        manifest = create_manifest(uid, group_id, image or folder, texts, sha, sensor_id=status.sensor_id)
        save_manifest(manifest, folder)
        log.debug("[CLAIMED] sensor=%s group=%s uid=%s files=%d", status.sensor_id, group_id, uid, len(moved))
        return folder, manifest

    def _prepare(self, item: tuple[Path, dict]):
        """Worker thread: image check + parse + analysis. Returns Prepared or the exception."""
        folder, manifest = item
        manifest["attempts"] = manifest.get("attempts", 0) + 1
        try:
            return prepare(folder, manifest, self.settings)
        except Exception as exc:  # noqa: BLE001
            if not isinstance(exc, PipelineError):
                log.exception("[UNEXPECTED] uid=%s", manifest.get("uid"))
            return exc

    def _fail(self, folder: Path, manifest: dict, reason: str, count_error: bool = True) -> None:
        manifest["status"] = FAILED
        manifest["error"] = reason
        save_manifest(manifest, folder)
        quarantine(folder, self.settings.error_dir, manifest["uid"], reason)
        if count_error:
            self._record_error(reason)

    def _process_claimed(self, items: list[tuple[Path, dict]]) -> None:
        if not items:
            return

        # 1. parse/analyse in parallel
        results = list(self.pool.map(self._prepare, items))
        good = []
        for (folder, manifest), res in zip(items, results):
            if isinstance(res, Exception):
                reason = str(res) if isinstance(res, PipelineError) else f"UNEXPECTED: {res!r}"
                self._fail(folder, manifest, reason)
            else:
                good.append(res)
        if not good:
            return

        # 2. one DB transaction for the whole batch
        try:
            saved = self.repo.save_batch([
                dict(record=p.record, analysis=p.analysis, uid=p.manifest["uid"],
                     content_hash=p.manifest.get("content_hash"),
                     image_file=p.manifest.get("image"), folder=p.archive_rel)
                for p in good
            ])
        except Exception as exc:  # noqa: BLE001 – DB locked / disk full / ...
            for p in good:
                p.manifest["error"] = f"DATABASE: {exc}"
                if p.manifest["attempts"] < DB_MAX_ATTEMPTS:
                    save_manifest(p.manifest, p.folder)        # stays CLAIMED -> retried
                else:
                    self._fail(p.folder, p.manifest, f"DATABASE: {exc}")
            log.error("[DB RETRY] %d inspection(s) attempt=%d: %s", len(good), good[0].manifest["attempts"], exc)
            self._record_error(f"DATABASE: {exc}")
            return

        # 3. finalize (manifest DONE + archive move) in parallel
        done = []
        for p, (db_id, action) in zip(good, saved):
            if action == "DUPLICATE":
                self._fail(p.folder, p.manifest, f"DUPLICATE of database id {db_id}", count_error=False)
                self.stats["duplicates"] += 1
                continue
            p.manifest.update(status=DONE, error=None, analysis_status=p.analysis.status,
                              database_id=db_id, archive=p.archive_rel)
            done.append((p, action))

        list(self.pool.map(lambda pa: self._finalize(pa[0]), done))

        for p, action in done:
            self.stats["processed"] += 1
            key = p.analysis.status.lower()
            if key in self.stats:
                self.stats[key] += 1
            log.info("[DONE] uid=%s status=%s action=%s", p.manifest["uid"], p.analysis.status, action)
        if done:
            self.stats["last_success_at"] = utc_now()

    def _finalize(self, p) -> None:
        save_manifest(p.manifest, p.folder)
        self._archive(p.folder, p.manifest)

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
        s = self.settings
        claimed: list[tuple[Path, dict]] = []
        known = {name for name, _ in s.sensor_sources()}
        for folder in sorted(p for p in s.processing_dir.iterdir() if p.is_dir()):
            manifest = load_manifest(folder)
            if manifest is None:
                manifest = rebuild_manifest(folder, s.supported_image_ext, s.supported_text_ext,
                                            known, s.sensor_id)
                if manifest is None:
                    continue
                log.warning("[RECOVER] rebuilt missing manifest for %s", folder.name)
                save_manifest(manifest, folder)
            status = manifest.get("status")
            if status == CLAIMED:
                claimed.append((folder, manifest))
            elif status == DONE:
                self._archive(folder, manifest)
        bs = max(1, s.batch_size)
        for i in range(0, len(claimed), bs):
            log.info("[RECOVER] %d inspection(s)", len(claimed[i:i + bs]))
            self._process_claimed(claimed[i:i + bs])
        return len(claimed)

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
                {"enabled": True, "backend": self.settings.upload_backend,
                 "statuses": None if self.settings.upload_statuses is None else sorted(self.settings.upload_statuses),
                 **self.upload_worker.stats}
                if self.upload_worker else {"enabled": False}
            ),
            "telegram": (
                {"enabled": True, **self.notify_worker.stats}
                if self.notify_worker else {"enabled": False}
            ),
            "sheets": (
                {"enabled": True, **self.sheets_worker.stats}
                if self.sheets_worker else {"enabled": False}
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
        log.info("IV4 Data Agent %s starting", read_version())
        log.info("Incoming   : %s", s.incoming_dir)
        for sensor, folder in s.sensor_sources():
            log.info("  sensor %-10s <- %s", sensor, folder)
        log.info("Processing : %s", s.processing_dir)
        log.info("Error      : %s", s.error_dir)
        log.info("Database   : %s", s.database_path)
        log.info("Settle     : %.1fs  Group timeout: %.0fs", s.settle_seconds, s.group_timeout)
        log.info("Workers    : %d threads, DB batch %d", s.worker_count(), s.batch_size)
        log.info("Archive    : %s", s.archive_dir)
        log.info("Retention  : OK=%s NG=%s rows=%s (days, 0=keep)",
                 s.retention_ok_days, s.retention_ng_days, s.retention_rows_days)
        log.info("Upload     : %s", (
            f"{s.upload_backend} ({'ALL' if s.upload_statuses is None else ','.join(sorted(s.upload_statuses))})"
            if s.upload_enabled else "disabled"))
        log.info("Telegram   : %s", "shift notifications ON" if s.telegram_enabled else "disabled")
        log.info("Sheets     : %s", f"every {s.sheets_interval:.0f}s" if s.sheets_enabled else "disabled")
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

        if s.sheets_enabled:
            from app.sheets import SheetsWorker

            self.sheets_worker = SheetsWorker(s, self.stop_event)
            self.sheets_worker.start()
        if s.telegram_enabled and s.telegram_token and s.telegram_chat_id:
            from app.notify import NotifyWorker

            self.notify_worker = NotifyWorker(s, self.stop_event)
            self.notify_worker.start()

        stop_flag = s.log_dir / STOP_FLAG
        stop_flag.unlink(missing_ok=True)      # leftover from an earlier `run stop`
        try:
            while not self.stop_event.is_set():
                if stop_flag.exists():         # `run stop` (works on Windows, where signals cannot be sent)
                    stop_flag.unlink(missing_ok=True)
                    log.info("Stop requested (run stop)")
                    self.stop()
                    break
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
            if self.sheets_worker is not None:
                self.sheets_worker.join(timeout=30)
            if self.notify_worker is not None:
                self.notify_worker.join(timeout=15)
            if self.maintenance is not None:
                self.maintenance.join(timeout=30)
            self.write_health()
            self.pool.shutdown(wait=True)
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
