"""
Parser -> Inspection Record -> Analysis -> Database for one claimed
inspection folder (processing/<uid>/).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.analysis.analyzer import analyze_inspection
from app.config import Settings, load_settings
from app.database.repository import DatabaseRepository
from app.parser.inspection_record import build_inspection_record

log = logging.getLogger(__name__)


class PipelineError(Exception):
    def __init__(self, stage: str, message: str):
        super().__init__(f"{stage}: {message}")
        self.stage = stage


@dataclass
class PipelineResult:
    uid: str
    inspection_id: str
    status: str            # PASS / FAIL / UNKNOWN
    action: str            # CREATED / UPDATED / DUPLICATE
    database_id: int
    archive_rel: str       # <YYYY-MM-DD>/<OK|NG|UNKNOWN>/<uid>, relative to archive_dir


STATUS_FOLDER = {"PASS": "OK", "FAIL": "NG"}


def archive_rel_path(uid: str, status: str, timestamp: str | None, created_at: str | None = None) -> str:
    """Day folder from sensor time (local), else from ingestion time."""
    day = None
    if timestamp and len(timestamp) >= 10 and timestamp[4] == "-":
        day = timestamp[:10]
    if day is None:
        day = (created_at or "")[:10] or datetime.now().strftime("%Y-%m-%d")
    return f"{day}/{STATUS_FOLDER.get(status, 'UNKNOWN')}/{uid}"


def verify_image(path: Path) -> None:
    try:
        from PIL import Image
    except ImportError:  # Pillow optional
        return
    try:
        with Image.open(path) as img:
            img.verify()
    except Exception as exc:  # noqa: BLE001
        raise PipelineError("IMAGE", f"{path.name} is not a valid image ({exc})") from exc


def run_pipeline(
    folder: Path,
    manifest: dict,
    repository: DatabaseRepository,
    settings: Settings,
) -> PipelineResult:

    uid = manifest["uid"]
    inspection_id = manifest["inspection_id"]

    if settings.verify_images and manifest.get("image"):
        verify_image(folder / manifest["image"])

    try:
        record = build_inspection_record(
            folder,
            inspection_id=inspection_id,
            date_format=settings.date_format,
            machine_id=settings.machine_id,
            camera_id=settings.sensor_id,
        )
    except Exception as exc:  # noqa: BLE001
        raise PipelineError("PARSER", str(exc)) from exc

    for w in record.warnings:
        log.warning("[PARSER] uid=%s %s", uid, w)

    try:
        analysis = analyze_inspection(
            record,
            score_threshold=settings.score_threshold,
            confidence_threshold=settings.confidence_threshold,
        )
    except Exception as exc:  # noqa: BLE001
        raise PipelineError("ANALYSIS", str(exc)) from exc

    archive_rel = archive_rel_path(uid, analysis.status, record.timestamp, manifest.get("created_at"))

    try:
        saved, action = repository.save_or_update_inspection(
            record,
            analysis,
            uid=uid,
            content_hash=manifest.get("content_hash"),
            image_file=manifest.get("image"),
            folder=archive_rel,
        )
    except Exception as exc:  # noqa: BLE001
        raise PipelineError("DATABASE", str(exc)) from exc

    return PipelineResult(
        uid=uid,
        inspection_id=record.inspection_id,
        status=analysis.status,
        action=action,
        database_id=saved.id,
        archive_rel=archive_rel,
    )


def process_inspection(inspection_id: str, processing_folder: Path) -> str:
    """
    Backwards-compatible helper used by the old README / scripts:
    run the pipeline on processing_folder/<inspection_id>/ directly.
    """
    settings = load_settings()
    folder = Path(processing_folder) / inspection_id
    if not folder.exists():
        log.error("[PIPELINE ERROR] Folder not found: %s", folder)
        return "ERROR"
    repo = DatabaseRepository(settings.database_path)
    try:
        result = run_pipeline(
            folder,
            {"uid": inspection_id, "inspection_id": inspection_id, "image": None},
            repo,
            settings,
        )
        log.info("[PIPELINE COMPLETE] %s", result)
        return "SUCCESS"
    except PipelineError as exc:
        log.error("[PIPELINE ERROR] %s", exc)
        return "ERROR"
    finally:
        repo.dispose()
