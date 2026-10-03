"""
File moves between incoming/ -> processing/<uid>/ -> error/<uid>/.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from app.ingestion.record import utc_now

log = logging.getLogger(__name__)


def move_files(files: list[Path], destination: Path) -> list[Path]:
    destination.mkdir(parents=True, exist_ok=True)
    moved: list[Path] = []
    for file_path in files:
        if not file_path.exists():
            continue
        target = destination / file_path.name
        shutil.move(str(file_path), str(target))
        moved.append(target)
    return moved


def unique_dir(parent: Path, name: str) -> Path:
    candidate = parent / name
    n = 1
    while candidate.exists():
        candidate = parent / f"{name}__{n}"
        n += 1
    return candidate


def quarantine(
    files_or_folder: list[Path] | Path,
    error_root: Path,
    name: str,
    reason: str,
) -> Path:
    """
    Move a failed inspection (folder or loose files) into error/<name>/
    and write ERROR.txt explaining why. Never deletes anything.
    """
    error_root.mkdir(parents=True, exist_ok=True)
    target = unique_dir(error_root, name)

    if isinstance(files_or_folder, Path):
        shutil.move(str(files_or_folder), str(target))
    else:
        move_files(files_or_folder, target)

    target.mkdir(parents=True, exist_ok=True)
    (target / "ERROR.txt").write_text(
        f"time={utc_now()}\nreason={reason}\n",
        encoding="utf-8",
    )
    log.warning("[ERROR] moved to %s reason=%s", target.name, reason)
    return target
