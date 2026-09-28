from pathlib import Path
import shutil


def move_inspection_files(
    files: list[Path],
    destination: Path,
) -> list[Path]:
    """
    Move all files belonging to one inspection
    into a dedicated folder.
    """

    destination.mkdir(
        parents=True,
        exist_ok=True,
    )

    moved_files = []

    for file_path in files:

        if not file_path.exists():
            continue

        destination_file = (
            destination /
            file_path.name
        )

        shutil.move(
            str(file_path),
            str(destination_file),
        )

        moved_files.append(
            destination_file
        )

    return moved_files