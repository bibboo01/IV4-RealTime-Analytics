"""
Upload queue (NOT wired into the realtime agent yet – Phase 7).

Only inspections whose manifest status is DONE and that have not been
uploaded yet are sent. The uploader is pluggable so Google Drive / S3 /
SharePoint can replace the mock without touching the core pipeline.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from app.config import Settings, load_settings
from app.ingestion.record import DONE, load_manifest, save_manifest, utc_now
from app.upload.mock_uploader import upload_inspection

log = logging.getLogger(__name__)

Uploader = Callable[[Path, Path], bool]


def pending_uploads(processing_dir: Path) -> list[tuple[Path, dict]]:
    out = []
    if not processing_dir.exists():
        return out
    for folder in sorted(p for p in processing_dir.iterdir() if p.is_dir()):
        m = load_manifest(folder)
        if m and m.get("status") == DONE and not m.get("uploaded_at"):
            out.append((folder, m))
    return out


def process_upload_queue(
    settings: Settings | None = None,
    uploader: Uploader = upload_inspection,
) -> int:
    settings = settings or load_settings()
    uploaded = 0
    for folder, manifest in pending_uploads(settings.processing_dir):
        try:
            if uploader(folder, settings.uploaded_dir):
                manifest["uploaded_at"] = utc_now()
                save_manifest(manifest, folder)
                uploaded += 1
        except Exception:  # noqa: BLE001
            log.exception("[UPLOAD ERROR] %s", folder.name)
    return uploaded
