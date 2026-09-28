from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any
import json

from app.parser.txt_parser import parse_txt


@dataclass
class InspectionRecord:
    """
    Normalized data model for one IV4 inspection.

    NOTE:
    This structure is currently based on Mock Data.
    It must be adjusted when the real IV4 TXT format is available.
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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(
            self.to_dict(),
            indent=indent,
            ensure_ascii=False,
        )


def build_inspection_record(
    inspection_folder: str | Path,
) -> InspectionRecord:

    inspection_folder = Path(inspection_folder)

    txt_files = sorted(
        inspection_folder.glob("*.txt")
    )

    if not txt_files:
        raise FileNotFoundError(
            f"No TXT files found in: "
            f"{inspection_folder}"
        )

    parsed_files: dict[str, dict[str, Any]] = {}

    for txt_file in txt_files:
        parsed_files[txt_file.name] = parse_txt(
            txt_file
        )

    # --------------------------------------------------
    # Find main inspection data
    # --------------------------------------------------

    main_data: dict[str, Any] = {}

    for filename, data in parsed_files.items():

        if not filename.lower().endswith(
            "_result.txt"
        ):
            main_data.update(data)

    # --------------------------------------------------
    # Merge result data
    # --------------------------------------------------

    result_data: dict[str, Any] = {}

    for filename, data in parsed_files.items():

        if filename.lower().endswith(
            "_result.txt"
        ):
            result_data.update(data)

    # Result file takes precedence for result-related fields
    merged = {
        **main_data,
        **result_data,
    }

    inspection_id = str(
        merged.get(
            "inspection_id",
            inspection_folder.name,
        )
    )

    return InspectionRecord(
        inspection_id=inspection_id,

        timestamp=merged.get(
            "timestamp"
        ),

        machine_id=merged.get(
            "machine_id"
        ),

        camera_id=merged.get(
            "camera_id"
        ),

        result=merged.get(
            "result"
        ),

        score=merged.get(
            "score"
        ),

        width=merged.get(
            "width"
        ),

        height=merged.get(
            "height"
        ),

        defect_count=merged.get(
            "defect_count"
        ),

        inspection_time_ms=merged.get(
            "inspection_time_ms"
        ),

        confidence=merged.get(
            "confidence"
        ),

        source_files=[
            file.name
            for file in txt_files
        ],
    )