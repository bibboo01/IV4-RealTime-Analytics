from pathlib import Path
from collections import defaultdict

from app.ingestion.record import (
    create_inspection_record,
    save_record,
)

from app.ingestion.lifecycle import (
    move_inspection_files,
)

IMAGE_EXTENSIONS = {".jpg", ".jpeg"}
TEXT_EXTENSION = ".txt"


def get_group_id(file_path: Path) -> str:
    """
    Extract group ID from filename.
    """
    return file_path.stem.split("_")[0]


def find_groups(folder: Path):
    """
    Group files by inspection ID.
    """
    groups = defaultdict(list)

    if not folder.exists():
        return groups

    for file_path in folder.iterdir():

        if not file_path.is_file():
            continue

        extension = file_path.suffix.lower()

        if (
            extension not in IMAGE_EXTENSIONS
            and extension != TEXT_EXTENSION
        ):
            continue

        group_id = get_group_id(file_path)

        groups[group_id].append(file_path)

    return groups

def validate_group(files: list[Path]) -> dict:

    image_files = [
        file
        for file in files
        if file.suffix.lower()
        in IMAGE_EXTENSIONS
    ]

    text_files = [
        file
        for file in files
        if file.suffix.lower()
        == TEXT_EXTENSION
    ]

    image_count = len(image_files)
    text_count = len(text_files)

    if image_count == 1 and text_count == 2:

        status = "COMPLETE"

    elif image_count <= 1 and text_count <= 2:

        status = "WAITING"

    else:

        status = "INVALID"

    return {
        "status": status,
        "image_files": image_files,
        "text_files": text_files,
        "image_count": image_count,
        "text_count": text_count,
    }

def process_group(
    group_id: str,
    incoming_folder: Path,
    processing_folder: Path,
):

    groups = find_groups(incoming_folder)

    files = groups.get(group_id, [])

    if not files:

        print(
            f"[MATCHER] "
            f"No files found for group={group_id}"
        )

        return "WAITING"

    result = validate_group(files)

    print()
    print(
        f"[MATCH GROUP] id={group_id}"
    )

    for file_path in sorted(files):

        print(
            f"  - {file_path.name}"
        )

    print(
        f"  Images : "
        f"{result['image_count']}"
    )

    print(
        f"  Texts  : "
        f"{result['text_count']}"
    )

    print(
        f"  STATUS : "
        f"{result['status']}"
    )

    # -----------------------------------------
    # Not complete yet
    # -----------------------------------------

    if result["status"] != "COMPLETE":

        return "WAITING"

    # -----------------------------------------
    # Check if already processed
    # -----------------------------------------

    inspection_folder = (
        processing_folder /
        group_id
    )

    record_file = (
        inspection_folder /
        f"{group_id}.json"
    )

    if record_file.exists():

        print(
            f"  [SKIP] "
            f"Record already exists: "
            f"{group_id}.json"
        )

        return "SKIP"

    # -----------------------------------------
    # Create processing folder
    # -----------------------------------------

    inspection_folder.mkdir(
        parents=True,
        exist_ok=True,
    )

    # -----------------------------------------
    # Move files
    # -----------------------------------------

    moved_files = move_inspection_files(
        files=files,
        destination=inspection_folder,
    )

    print(
        f"  [MOVED] "
        f"{len(moved_files)} files"
    )

    # -----------------------------------------
    # Update paths
    # -----------------------------------------

    image_file = (
        inspection_folder /
        result["image_files"][0].name
    )

    text_files = [
        inspection_folder /
        file.name
        for file in result["text_files"]
    ]

    # -----------------------------------------
    # Create record
    # -----------------------------------------

    record = create_inspection_record(
        inspection_id=group_id,
        image_file=image_file,
        text_files=text_files,
        status="COMPLETE",
    )

    save_record(
        record,
        inspection_folder,
    )

    print(
        f"  [RECORD CREATED] "
        f"{group_id}.json"
    )

    return "COMPLETE"

if __name__ == "__main__":

    base_dir = Path(__file__).resolve().parents[2]

    incoming_folder = (
        base_dir /
        "data" /
        "incoming"
    )

    processing_folder = (
        base_dir /
        "data" /
        "processing"
    )

    print("=" * 60)
    print("IV4 File Matcher")
    print("=" * 60)
    print(f"Incoming  : {incoming_folder}")
    print(f"Processing: {processing_folder}")
    print("=" * 60)

    process_groups(
        incoming_folder,
        processing_folder,
    )