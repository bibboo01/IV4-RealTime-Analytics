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

INSPECTION_FOLDER = (
    BASE_DIR /
    "data" /
    "processing" /
    "006"
)

DATABASE_PATH = (
    BASE_DIR /
    "data" /
    "database" /
    "iv4.db"
)


def main():

    print("=" * 60)
    print("IV4 Database Test")
    print("=" * 60)

    # --------------------------------------------------
    # Step 1: Build Record
    # --------------------------------------------------

    print()
    print("[STEP 1] Building Inspection Record")

    record = build_inspection_record(
        INSPECTION_FOLDER
    )

    print(
        f"  Inspection ID : "
        f"{record.inspection_id}"
    )

    # --------------------------------------------------
    # Step 2: Analysis
    # --------------------------------------------------

    print()
    print("[STEP 2] Running Analysis")

    analysis = analyze_inspection(
        record
    )

    print(
        f"  Status : "
        f"{analysis.status}"
    )

    # --------------------------------------------------
    # Step 3: Database
    # --------------------------------------------------

    print()
    print("[STEP 3] Connecting Database")

    repository = DatabaseRepository(
        DATABASE_PATH
    )

    print(
        f"  Database : "
        f"{DATABASE_PATH}"
    )

    # --------------------------------------------------
    # Step 4: Save / Update
    # --------------------------------------------------

    print()
    print("[STEP 4] Save / Update Inspection")

    saved, action = (
        repository.save_or_update_inspection(
            record,
            analysis,
        )
    )

    print(
        f"  Action : {action}"
    )

    print(
        f"  Database ID : "
        f"{saved.id}"
    )

    print(
        f"  Inspection ID : "
        f"{saved.inspection_id}"
    )

    print(
        f"  Status : "
        f"{saved.analysis_status}"
    )

    # --------------------------------------------------
    # Step 5: Read
    # --------------------------------------------------

    print()
    print("[STEP 5] Reading Inspection")

    found = repository.get_by_inspection_id(
        record.inspection_id
    )

    if found is None:

        print(
            "  ERROR: Record not found"
        )

        return

    print(
        f"  inspection_id = "
        f"{found.inspection_id}"
    )

    print(
        f"  result = "
        f"{found.result}"
    )

    print(
        f"  score = "
        f"{found.score}"
    )

    print(
        f"  status = "
        f"{found.analysis_status}"
    )

    # --------------------------------------------------
    # Step 6: Count
    # --------------------------------------------------

    print()
    print("[STEP 6] Database Count")

    count = repository.count()

    print(
        f"  Total inspections = "
        f"{count}"
    )

    # --------------------------------------------------
    # Step 7: Duplicate Protection
    # --------------------------------------------------

    print()
    print("[STEP 7] Duplicate Protection Test")

    saved_again, action_again = (
        repository.save_or_update_inspection(
            record,
            analysis,
        )
    )

    print(
        f"  Action : "
        f"{action_again}"
    )

    print(
        f"  Database ID : "
        f"{saved_again.id}"
    )

    print(
        f"  Inspection ID : "
        f"{saved_again.inspection_id}"
    )

    print(
        f"  Total inspections = "
        f"{repository.count()}"
    )

    print()
    print("=" * 60)
    print("DATABASE TEST COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()