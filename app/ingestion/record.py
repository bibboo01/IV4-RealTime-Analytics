from pathlib import Path
from datetime import datetime, timezone
import json


def create_inspection_record(
    inspection_id: str,
    image_file: Path,
    text_files: list[Path],
    status: str,
) -> dict:

    return {
        "inspection_id": inspection_id,
        "image": str(image_file),
        "text_files": [
            str(file)
            for file in sorted(text_files)
        ],
        "status": status,
        "created_at": datetime.now(
            timezone.utc
        ).isoformat(),
    }


def save_record(
    record: dict,
    output_folder: Path,
) -> Path:

    output_folder.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_file = (
        output_folder
        / f"{record['inspection_id']}.json"
    )

    with output_file.open(
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            record,
            file,
            indent=4,
            ensure_ascii=False,
        )

    return output_file