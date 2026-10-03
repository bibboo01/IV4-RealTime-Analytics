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
from app.ingestion.record import DONE, load_manifest, save_manifest, utc_now
from app.upload.mock_uploader import upload_inspection as mock_upload

log = logging.getLogger(__name__)

# (inspection_folder, manifest) -> remote reference (str) or True on success
Uploader = Callable[[Path, dict], "str | bool"]


def pending_uploads(processing_dir: Path) -> list[tuple[Path, dict]]:
    out = []
    if not processing_dir.exists():
        return out
    for folder in sorted(p for p in processing_dir.iterdir() if p.is_dir()):
        m = load_manifest(folder)
        if m and m.get("status") == DONE and not m.get("uploaded_at"):
            out.append((folder, m))
    return out


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
) -> int:
    settings = settings or load_settings()
    uploader = uploader or make_uploader(settings)
    uploaded = 0
    for folder, manifest in pending_uploads(settings.processing_dir)[: settings.upload_batch]:
        if stop_event is not None and stop_event.is_set():
            break
        try:
            ref = uploader(folder, manifest)
        except Exception as exc:  # noqa: BLE001
            from app.upload.google_drive import DriveConfigError

            if isinstance(exc, DriveConfigError):
                raise  # needs a human; caller logs once and backs off
            manifest["upload_error"] = str(exc)[:500]
            manifest["upload_attempts"] = manifest.get("upload_attempts", 0) + 1
            save_manifest(manifest, folder)
            log.warning("[UPLOAD RETRY] %s: %s", folder.name, exc)
            continue
        if ref:
            manifest["uploaded_at"] = utc_now()
            manifest["upload_backend"] = settings.upload_backend
            if isinstance(ref, str):
                manifest["upload_ref"] = ref
            manifest.pop("upload_error", None)
            save_manifest(manifest, folder)
            uploaded += 1
    return uploaded


class UploadWorker(threading.Thread):
    """Background thread that drains the upload queue every upload_interval."""

    def __init__(self, settings: Settings, stop_event: threading.Event, uploader: Uploader | None = None):
        super().__init__(name="upload-worker", daemon=True)
        self.settings = settings
        self.stop_event = stop_event
        self._uploader = uploader
        self.stats = {"uploaded": 0, "last_upload_at": None, "last_upload_error": None, "pending": 0}

    def run(self) -> None:
        from app.upload.google_drive import DriveConfigError

        log.info("[UPLOAD] worker started backend=%s interval=%.0fs",
                 self.settings.upload_backend, self.settings.upload_interval)
        while not self.stop_event.is_set():
            try:
                if self._uploader is None:
                    self._uploader = make_uploader(self.settings)
                n = process_upload_queue(self.settings, self._uploader, self.stop_event)
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
                self.stats["pending"] = len(pending_uploads(self.settings.processing_dir))
            except OSError:
                pass
            self.stop_event.wait(self.settings.upload_interval)
