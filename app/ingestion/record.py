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


def content_hash(files: list[Path], sensor_id: str | None = None) -> str:
    """
    SHA-256 over sensor + file names + bytes. Identical re-drops from the
    SAME sensor get identical hashes; another sensor never collides.
    """
    h = hashlib.sha256()
    if sensor_id:
        h.update(sensor_id.encode() + b"\0")
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


def save_manifest(manifest: dict, folder: Path, durable: bool = False) -> Path:
    """
    Atomic write (tmp + replace): a process crash never leaves half a JSON.
    durable=True also fsyncs (survives power loss) at ~0.5-5 ms per call; by
    default we skip it because recovery rebuilds a lost/corrupt manifest
    from the files themselves (rebuild_manifest) and the DB is the record.
    """
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / MANIFEST_NAME
    tmp = folder / (MANIFEST_NAME + ".tmp")
    manifest["updated_at"] = utc_now()
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)
        if durable:
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


def rebuild_manifest(
    folder: Path,
    image_ext: frozenset[str],
    text_ext: frozenset[str],
    known_sensors: set[str],
    default_sensor: str | None,
) -> dict | None:
    """
    Recreate a CLAIMED manifest for processing/<uid>/ whose manifest.json is
    missing or corrupt (e.g. power loss right after the claim).
    uid format: [<sensor>__]<group>__<YYYYmmddTHHMMSSffffff>
    """
    files = sorted(p for p in folder.iterdir() if p.is_file() and not p.name.startswith(MANIFEST_NAME))
    images = [f for f in files if f.suffix.lower() in image_ext]
    texts = [f for f in files if f.suffix.lower() in text_ext]
    if not images and not texts:
        return None
    parts = folder.name.split("__")
    sensor = default_sensor
    if len(parts) >= 3 and parts[0] in known_sensors:
        sensor, parts = parts[0], parts[1:]
    group = "__".join(parts[:-1]) if len(parts) >= 2 else folder.name
    m = create_manifest(folder.name, group, images[0] if images else folder, texts,
                        content_hash(images + texts, sensor), sensor_id=sensor)
    m["rebuilt"] = True
    return m
