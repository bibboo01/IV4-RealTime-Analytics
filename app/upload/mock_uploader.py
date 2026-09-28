from pathlib import Path
import shutil


def upload_inspection(
    inspection_folder: Path,
    uploaded_folder: Path,
) -> bool:

    inspection_id = inspection_folder.name

    destination = (
        uploaded_folder /
        inspection_id
    )

    destination.mkdir(
        parents=True,
        exist_ok=True,
    )

    print(
        f"[UPLOAD] "
        f"inspection={inspection_id}"
    )

    for file_path in inspection_folder.iterdir():

        if not file_path.is_file():
            continue

        destination_file = (
            destination /
            file_path.name
        )

        shutil.copy2(
            file_path,
            destination_file,
        )

        print(
            f"  [COPY] "
            f"{file_path.name}"
        )

    print(
        f"[UPLOAD SUCCESS] "
        f"inspection={inspection_id}"
    )

    return True