from __future__ import annotations

from pathlib import Path

from app.parser.inspection_record import (
    build_inspection_record,
)

from app.analysis.analyzer import (
    analyze_inspection,
)

from app.database.repository import (
    DatabaseRepository,
)


BASE_DIR = Path(__file__).resolve().parents[1]

DATABASE_PATH = (
    BASE_DIR
    / "data"
    / "database"
    / "iv4.db"
)


def process_inspection(
    inspection_id: str,
    processing_folder: Path,
) -> str:

    inspection_folder = (
        processing_folder
        / inspection_id
    )

    print(
        f"[PIPELINE] "
        f"inspection={inspection_id}"
    )

    # --------------------------------------------------
    # Check inspection folder
    # --------------------------------------------------

    if not inspection_folder.exists():

        print(
            f"[PIPELINE ERROR] "
            f"Folder not found: "
            f"{inspection_folder}"
        )

        return "ERROR"

    # --------------------------------------------------
    # Step 1: Parser
    # --------------------------------------------------

    print(
        f"[PARSER] "
        f"inspection={inspection_id}"
    )

    try:

        record = build_inspection_record(
            inspection_folder
        )

    except Exception as exc:

        print(
            f"[PARSER ERROR] "
            f"inspection={inspection_id} "
            f"error={exc}"
        )

        return "ERROR"

    print(
        f"  inspection_id = "
        f"{record.inspection_id}"
    )

    # --------------------------------------------------
    # Step 2: Analysis
    # --------------------------------------------------

    print(
        f"[ANALYSIS] "
        f"inspection={inspection_id}"
    )

    try:

        analysis = analyze_inspection(
            record
        )

    except Exception as exc:

        print(
            f"[ANALYSIS ERROR] "
            f"inspection={inspection_id} "
            f"error={exc}"
        )

        return "ERROR"

    print(
        f"  status = "
        f"{analysis.status}"
    )

    print(
        f"  score = "
        f"{analysis.score}"
    )

    print(
        f"  confidence = "
        f"{analysis.confidence}"
    )

    # --------------------------------------------------
    # Step 3: Database
    # --------------------------------------------------

    print(
        f"[DATABASE] "
        f"inspection={inspection_id}"
    )

    try:

        repository = DatabaseRepository(
            DATABASE_PATH
        )

        saved, action = (
            repository.save_or_update_inspection(
                record,
                analysis,
            )
        )

    except Exception as exc:

        print(
            f"[DATABASE ERROR] "
            f"inspection={inspection_id} "
            f"error={exc}"
        )

        return "ERROR"

    print(
        f"  action = {action}"
    )

    print(
        f"  database_id = {saved.id}"
    )

    print(
        f"  status = "
        f"{saved.analysis_status}"
    )

    # --------------------------------------------------
    # Complete
    # --------------------------------------------------

    print(
        f"[PIPELINE COMPLETE] "
        f"inspection={inspection_id}"
    )

    return "SUCCESS"