from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.parser.txt_parser import parse_txt


@dataclass
class InspectionRecord:
    """
    Normalized data model for one IV4 inspection.

    NOTE:
    Typed fields are based on Mock Data. Everything parsed from the TXT
    files is ALSO kept in ``raw`` (and stored in the DB), so no data is lost
    when the real IV4 format has fields we have not mapped yet.
    """

    inspection_id: str

    timestamp: str | None = None
    machine_id: str | None = None
    camera_id: str | None = None

    result: str | None = None
    score: float | None = None

    width: float | None = None
    height: float | None = None

    defect_count: int | None = None
    inspection_time_ms: int | None = None
    confidence: float | None = None

    source_files: list[str] | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False, default=str)


def _as_float(name: str, value: Any, warnings: list[str]) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        warnings.append(f"{name}: unexpected boolean {value!r}")
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        warnings.append(f"{name}: not a number {value!r}")
        return None


def _as_int(name: str, value: Any, warnings: list[str]) -> int | None:
    f = _as_float(name, value, warnings)
    if f is None:
        return None
    if not f.is_integer():
        warnings.append(f"{name}: not an integer {value!r}")
    return int(f)


def _as_str(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def build_inspection_record(
    inspection_folder: str | Path,
    inspection_id: str | None = None,
) -> InspectionRecord:

    inspection_folder = Path(inspection_folder)

    txt_files = sorted(inspection_folder.glob("*.txt"))
    txt_files += sorted(inspection_folder.glob("*.TXT"))
    txt_files = sorted(set(txt_files))

    if not txt_files:
        raise FileNotFoundError(f"No TXT files found in: {inspection_folder}")

    main_data: dict[str, Any] = {}
    result_data: dict[str, Any] = {}

    for txt_file in txt_files:
        data = parse_txt(txt_file)
        if txt_file.stem.lower().endswith("_result"):
            result_data.update(data)
        else:
            main_data.update(data)

    # Result file takes precedence for result-related fields
    merged = {**main_data, **result_data}

    warnings: list[str] = []

    file_id = inspection_id or inspection_folder.name
    txt_id = _as_str(merged.get("inspection_id"))
    if txt_id and txt_id != file_id:
        warnings.append(f"inspection_id in TXT ({txt_id}) != filename ({file_id})")

    return InspectionRecord(
        inspection_id=txt_id or file_id,
        timestamp=_as_str(merged.get("timestamp")),
        machine_id=_as_str(merged.get("machine_id")),
        camera_id=_as_str(merged.get("camera_id")),
        result=_as_str(merged.get("result")),
        score=_as_float("score", merged.get("score"), warnings),
        width=_as_float("width", merged.get("width"), warnings),
        height=_as_float("height", merged.get("height"), warnings),
        defect_count=_as_int("defect_count", merged.get("defect_count"), warnings),
        inspection_time_ms=_as_int(
            "inspection_time_ms", merged.get("inspection_time_ms"), warnings
        ),
        confidence=_as_float("confidence", merged.get("confidence"), warnings),
        source_files=[f.name for f in txt_files],
        raw=merged,
        warnings=warnings,
    )
