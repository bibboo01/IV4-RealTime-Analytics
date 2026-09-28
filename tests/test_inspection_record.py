from pathlib import Path

from app.parser.inspection_record import (
    build_inspection_record,
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
    print("IV4 Inspection Record Test")
    print("=" * 60)

    print()
    print(
        f"[PROCESSING] "
        f"{INSPECTION_FOLDER}"
    )

    record = build_inspection_record(
        INSPECTION_FOLDER
    )

    print()
    print("[INSPECTION RECORD]")

    for key, value in record.to_dict().items():

        print(
            f"  {key:<22} = {value!r}"
        )

    print()
    print("[JSON]")

    print(
        record.to_json()
    )

    print()
    print("=" * 60)
    print("INSPECTION RECORD TEST COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()