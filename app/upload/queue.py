from pathlib import Path

from app.upload.mock_uploader import (
    upload_inspection,
)


BASE_DIR = Path(__file__).resolve().parents[2]

PROCESSING_FOLDER = (
    BASE_DIR /
    "data" /
    "processing"
)

UPLOADED_FOLDER = (
    BASE_DIR /
    "data" /
    "uploaded"
)


def process_upload_queue():

    if not PROCESSING_FOLDER.exists():
        return

    inspection_folders = [
        path
        for path in PROCESSING_FOLDER.iterdir()
        if path.is_dir()
    ]

    if not inspection_folders:

        print(
            "[UPLOAD QUEUE] "
            "No pending inspections."
        )

        return

    print()
    print("=" * 60)
    print("Upload Queue")
    print("=" * 60)

    for inspection_folder in sorted(
        inspection_folders
    ):

        inspection_id = (
            inspection_folder.name
        )

        print()
        print(
            f"[QUEUE] "
            f"inspection={inspection_id}"
        )

        try:

            success = upload_inspection(
                inspection_folder=inspection_folder,
                uploaded_folder=UPLOADED_FOLDER,
            )

            if success:

                print(
                    f"[QUEUE COMPLETE] "
                    f"{inspection_id}"
                )

        except Exception as exc:

            print(
                f"[QUEUE ERROR] "
                f"{inspection_id}: "
                f"{exc}"
            )

    print()
    print("=" * 60)