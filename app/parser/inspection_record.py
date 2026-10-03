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

    # KEYENCE IV4 specific
    program_no: int | None = None
    trigger_no: int | None = None
    tools: list[dict[str, Any]] = field(default_factory=list)
    source_format: str = "keyvalue"

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
    date_format: str = "%d/%m/%Y",
    machine_id: str | None = None,
    camera_id: str | None = None,
) -> InspectionRecord:

    inspection_folder = Path(inspection_folder)

    txt_files = sorted(
        {p for p in inspection_folder.iterdir() if p.is_file() and p.suffix.lower() == ".txt"}
    )

    if not txt_files:
        raise FileNotFoundError(f"No TXT files found in: {inspection_folder}")

    main_data: dict[str, Any] = {}
    result_data: dict[str, Any] = {}

    for txt_file in txt_files:
        data = parse_txt(txt_file, date_format=date_format)
        if txt_file.stem.lower().endswith("_result"):
            result_data.update(data)
        else:
            main_data.update(data)

    # Result file takes precedence for result-related fields
    merged = {**main_data, **result_data}

    warnings: list[str] = []
    file_id = inspection_id or inspection_folder.name
    source_files = [f.name for f in txt_files]

    if merged.get("format") == "iv4":
        return _from_iv4(merged, file_id, source_files, warnings, machine_id, camera_id)

    txt_id = _as_str(merged.get("inspection_id"))
    if txt_id and txt_id != file_id:
        warnings.append(f"inspection_id in TXT ({txt_id}) != filename ({file_id})")

    return InspectionRecord(
        inspection_id=txt_id or file_id,
        timestamp=_as_str(merged.get("timestamp")),
        machine_id=_as_str(merged.get("machine_id")) or machine_id,
        camera_id=_as_str(merged.get("camera_id")) or camera_id,
        result=_as_str(merged.get("result")),
        score=_as_float("score", merged.get("score"), warnings),
        width=_as_float("width", merged.get("width"), warnings),
        height=_as_float("height", merged.get("height"), warnings),
        defect_count=_as_int("defect_count", merged.get("defect_count"), warnings),
        inspection_time_ms=_as_int(
            "inspection_time_ms", merged.get("inspection_time_ms"), warnings
        ),
        confidence=_as_float("confidence", merged.get("confidence"), warnings),
        source_files=source_files,
        raw=merged,
        warnings=warnings,
    )


def _from_iv4(
    data: dict[str, Any],
    file_id: str,
    source_files: list[str],
    warnings: list[str],
    machine_id: str | None,
    camera_id: str | None,
) -> InspectionRecord:
    """
    Map a parsed KEYENCE IV4 result file.

    * inspection_id = filename (e.g. 00001_03102026_181913); the sensor's
      own counter is kept in trigger_no
    * score         = lowest tool value (the weakest tool decides)
    * defect_count  = number of tools judged NG
    """
    if data.get("timestamp") is None:
        warnings.append(f"cannot parse 'Time and Date': {data.get('date_raw')!r}")

    tools = data.get("tools") or []
    values = [t["value"] for t in tools if isinstance(t.get("value"), (int, float)) and not isinstance(t.get("value"), bool)]
    ng_tools = [t for t in tools if (t.get("status") or "").upper() == "NG"]

    if data.get("result") is None:
        warnings.append("'Total Status' missing")

    return InspectionRecord(
        inspection_id=file_id,
        timestamp=data.get("timestamp"),
        machine_id=machine_id,
        camera_id=camera_id,
        result=data.get("result"),
        score=float(min(values)) if values else None,
        defect_count=len(ng_tools) if tools else None,
        inspection_time_ms=_as_int("TIME[ms]", data.get("inspection_time_ms"), warnings),
        program_no=_as_int("Program No.", data.get("program_no"), warnings),
        trigger_no=_as_int("Trigger No.", data.get("trigger_no"), warnings),
        tools=tools,
        source_format="iv4",
        source_files=source_files,
        raw=data,
        warnings=warnings,
    )
