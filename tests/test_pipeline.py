from pathlib import Path

from app.pipeline import (
    process_inspection,
)


BASE_DIR = Path(__file__).resolve().parents[1]

PROCESSING_FOLDER = (
    BASE_DIR
    / "data"
    / "processing"
)


def main():

    print("=" * 60)
    print("IV4 Pipeline Test")
    print("=" * 60)

    result = process_inspection(
        inspection_id="006",
        processing_folder=PROCESSING_FOLDER,
    )

    print()

    print(
        f"[PIPELINE RESULT] {result}"
    )

    print()
    print("=" * 60)
    print("PIPELINE TEST COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()