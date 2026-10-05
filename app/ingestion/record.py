"""
Ingestion manifest (``<uid>.json``) stored next to the inspection files.
It tracks lifecycle state so the agent can recover after a crash.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

MANIFEST_NAME = "manifest.json"

# Lifecycle states
CLAIMED = "CLAIMED"        # files moved into processing/<uid>/
DONE = "DONE"              # saved to database
FAILED = "FAILED"          # moved to error/


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_uid(group_id: str, when: datetime | None = None, sensor: str | None = None) -> str:
    """
    Unique, sortable ID for one physical inspection.

    IV4 file counters can reset (power cycle, counter roll-over), so the
    filename alone is NOT unique. The UID adds the UTC arrival time.
    """
    when = when or datetime.now(timezone.utc)
    prefix = f"{sensor}__" if sensor else ""
    return f"{prefix}{group_id}__{when.strftime('%Y%m%dT%H%M%S%f')}"


def content_hash(files: list[Path]) -> str:
    """SHA-256 over file names + bytes; identical drops get identical hashes."""
    h = hashlib.sha256()
    for f in sorted(files, key=lambda p: p.name.lower()):
        h.update(f.name.lower().encode())
        h.update(b"\0")
        with f.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        h.update(b"\0")
    return h.hexdigest()


def create_manifest(
    uid: str,
    inspection_id: str,
    image_file: Path,
    text_files: list[Path],
    sha256: str,
    sensor_id: str | None = None,
) -> dict:
    return {
        "uid": uid,
        "inspection_id": inspection_id,
        "sensor_id": sensor_id,
        "image": image_file.name,
        "text_files": sorted(f.name for f in text_files),
        "content_hash": sha256,
        "status": CLAIMED,
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "error": None,
    }


def save_manifest(manifest: dict, folder: Path) -> Path:
    """Atomic write (tmp + replace) so a crash never leaves half a JSON."""
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / MANIFEST_NAME
    tmp = folder / (MANIFEST_NAME + ".tmp")
    manifest["updated_at"] = utc_now()
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, target)
    return target


def load_manifest(folder: Path) -> dict | None:
    path = folder / MANIFEST_NAME
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
