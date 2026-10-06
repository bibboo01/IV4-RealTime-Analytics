"""
Upload queue: sends finished inspections (manifest status DONE) to online
storage. Runs in its own thread inside the agent when IV4_UPLOAD_ENABLED=true,
so a slow or offline network never blocks realtime ingestion. Anything that
fails stays pending and is retried on the next round.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable

from app.config import Settings, load_settings
from app.database.repository import DatabaseRepository
from app.ingestion.record import load_manifest, utc_now
from app.upload.mock_uploader import upload_inspection as mock_upload

log = logging.getLogger(__name__)

# (inspection_folder, manifest) -> remote reference (str) or True on success
Uploader = Callable[[Path, dict], "str | bool"]


def pending_uploads(settings: Settings, repo: DatabaseRepository) -> list[tuple[str, Path]]:
    """(uid, archive folder) of inspections still to upload, oldest first."""
    return [
        (uid, settings.archive_dir / rel)
        for uid, rel in repo.pending_uploads(settings.upload_statuses, settings.upload_batch)
    ]


def make_uploader(settings: Settings) -> Uploader:
    if settings.upload_backend == "mock":
        return lambda folder, manifest: mock_upload(folder, settings.uploaded_dir)
    if settings.upload_backend == "gdrive":
        from app.upload.google_drive import GoogleDriveUploader

        return GoogleDriveUploader(settings).upload_inspection
    raise ValueError(f"Unknown IV4_UPLOAD_BACKEND: {settings.upload_backend}")


def process_upload_queue(
    settings: Settings | None = None,
    uploader: Uploader | None = None,
    stop_event: threading.Event | None = None,
    repo: DatabaseRepository | None = None,
) -> int:
    """
    Upload one batch. Which inspections are sent is set by IV4_UPLOAD_STATUSES
    (default FAIL,UNKNOWN = NG images only; ALL = everything).
    """
    from app.upload.google_drive import DriveConfigError

    settings = settings or load_settings()
    own_repo = repo is None
    repo = repo or DatabaseRepository(settings.database_path)
    uploader = uploader or make_uploader(settings)
    uploaded = 0
    try:
        for uid, folder in pending_uploads(settings, repo):
            if stop_event is not None and stop_event.is_set():
                break
            if not folder.exists():
                log.warning("[UPLOAD] %s: files no longer on disk (retention?) - skipped", uid)
                repo.mark_uploaded(uid, "MISSING")
                continue
            manifest = load_manifest(folder) or {"uid": uid}
            try:
                ref = uploader(folder, manifest)
            except DriveConfigError:
                raise  # needs a human; caller logs once and backs off
            except Exception as exc:  # noqa: BLE001
                log.warning("[UPLOAD RETRY] %s: %s", uid, exc)
                continue
            if ref:
                repo.mark_uploaded(uid, ref if isinstance(ref, str) else settings.upload_backend)
                uploaded += 1
    finally:
        if own_repo:
            repo.dispose()
    return uploaded


class UploadWorker(threading.Thread):
    """Background thread that drains the upload queue every upload_interval."""

    def __init__(self, settings: Settings, stop_event: threading.Event, uploader: Uploader | None = None):
        super().__init__(name="upload-worker", daemon=True)
        self.settings = settings
        self.stop_event = stop_event
        self._uploader = uploader
        self.repo: DatabaseRepository | None = None
        self.stats = {"uploaded": 0, "last_upload_at": None, "last_upload_error": None, "pending": 0}

    def run(self) -> None:
        log.info("[UPLOAD] worker started backend=%s interval=%.0fs",
                 self.settings.upload_backend, self.settings.upload_interval)
        self.repo = DatabaseRepository(self.settings.database_path)
        try:
            self._loop()
        finally:
            self.repo.dispose()

    def _loop(self) -> None:
        from app.upload.google_drive import DriveConfigError

        while not self.stop_event.is_set():
            n = 0
            try:
                if self._uploader is None:
                    self._uploader = make_uploader(self.settings)
                n = process_upload_queue(self.settings, self._uploader, self.stop_event, self.repo)
                if n:
                    self.stats["uploaded"] += n
                    self.stats["last_upload_at"] = utc_now()
                self.stats["last_upload_error"] = None
            except DriveConfigError as exc:
                if self.stats["last_upload_error"] != str(exc):
                    log.error("[UPLOAD] configuration problem: %s", exc)
                self.stats["last_upload_error"] = str(exc)
                self._uploader = None  # rebuild after the human fixes it
            except Exception as exc:  # noqa: BLE001 – network down etc.
                log.warning("[UPLOAD] round failed: %s", exc)
                self.stats["last_upload_error"] = str(exc)[:500]
                self._uploader = None
            try:
                self.stats["pending"] = self.repo.count_pending_uploads(self.settings.upload_statuses)
            except Exception:  # noqa: BLE001
                pass
            if n >= self.settings.upload_batch:
                continue                     # full batch: more is waiting, do not sleep
            self.stop_event.wait(self.settings.upload_interval)
