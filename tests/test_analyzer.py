from pathlib import Path

from app.parser.inspection_record import (
    build_inspection_record,
)

from app.analysis.analyzer import (
    analyze_inspection,
)


BASE_DIR = Path(__file__).resolve().parents[1]

INSPECTION_FOLDER = (
    BASE_DIR /
    "data" /
    "processing" /
    "006"
)


def main():

    print("=" * 60)
    print("IV4 Analysis Engine Test")
    print("=" * 60)

    # --------------------------------------------------
    # Build Inspection Record
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
    # Analyze
    # --------------------------------------------------

    print()
    print("[STEP 2] Running Analysis")

    result = analyze_inspection(
        record
    )

    # --------------------------------------------------
    # Result
    # --------------------------------------------------

    print()
    print("[ANALYSIS RESULT]")

    print(
        f"  inspection_id     = "
        f"{result.inspection_id}"
    )

    print(
        f"  status            = "
        f"{result.status}"
    )

    print(
        f"  score             = "
        f"{result.score}"
    )

    print(
        f"  defect_count      = "
        f"{result.defect_count}"
    )

    print(
        f"  confidence        = "
        f"{result.confidence}"
    )

    print(
        f"  inspection_time_ms = "
        f"{result.inspection_time_ms}"
    )

    print(
        f"  reasons           = "
        f"{result.reasons}"
    )

    print()
    print("=" * 60)
    print("ANALYSIS TEST COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()