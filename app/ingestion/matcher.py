"""
Group incoming files into inspections and decide whether a group is
complete.

Grouping rule
-------------
The group key is the file stem with the result suffix removed:

    007.jpg          -> 007
    007.txt          -> 007
    007_result.txt   -> 007
    CAM1_0001.jpg    -> CAM1_0001     (underscores in the ID are kept)

This replaces the old ``stem.split("_")[0]`` rule, which would have merged
unrelated inspections as soon as real IV4 filenames contain underscores.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

RESULT_SUFFIX = "_result"

COMPLETE = "COMPLETE"
WAITING = "WAITING"
INVALID = "INVALID"


def get_group_id(file_path: Path) -> str:
    stem = file_path.stem
    if stem.lower().endswith(RESULT_SUFFIX):
        stem = stem[: -len(RESULT_SUFFIX)]
    return stem


def is_result_file(file_path: Path) -> bool:
    return file_path.stem.lower().endswith(RESULT_SUFFIX)


@dataclass
class GroupStatus:
    group_id: str
    status: str
    image_files: list[Path] = field(default_factory=list)
    text_files: list[Path] = field(default_factory=list)
    reason: str = ""
    sensor_id: str | None = None
    source_dir: Path | None = None
    uid_prefix: str | None = None

    @property
    def files(self) -> list[Path]:
        return sorted(self.image_files + self.text_files)


def find_groups(
    folder: Path,
    image_ext: frozenset[str],
    text_ext: frozenset[str],
) -> dict[str, list[Path]]:
    groups: dict[str, list[Path]] = defaultdict(list)
    if not folder.exists():
        return groups
    for file_path in folder.iterdir():
        if not file_path.is_file():
            continue
        ext = file_path.suffix.lower()
        if ext not in image_ext and ext not in text_ext:
            continue
        groups[get_group_id(file_path)].append(file_path)
    return groups


def validate_group(
    group_id: str,
    files: list[Path],
    image_ext: frozenset[str],
    text_ext: frozenset[str],
    expected_images: int = 1,
    expected_texts: int = 2,
) -> GroupStatus:
    images = sorted(f for f in files if f.suffix.lower() in image_ext)
    texts = sorted(f for f in files if f.suffix.lower() in text_ext)

    status = GroupStatus(group_id, WAITING, images, texts)

    main_texts = [t for t in texts if not is_result_file(t)]
    result_texts = [t for t in texts if is_result_file(t)]

    if len(images) > expected_images:
        status.status = INVALID
        status.reason = f"too many images ({len(images)} > {expected_images})"
    elif len(texts) > expected_texts:
        status.status = INVALID
        status.reason = f"too many text files ({len(texts)} > {expected_texts})"
    elif len(main_texts) > 1 or len(result_texts) > 1:
        status.status = INVALID
        status.reason = "duplicate main/result text file"
    elif len(images) == expected_images and len(texts) == expected_texts:
        if expected_texts >= 1 and not main_texts:
            status.status = INVALID
            status.reason = "main TXT missing"
        else:
            status.status = COMPLETE
    return status
