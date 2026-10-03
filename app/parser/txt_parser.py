from __future__ import annotations

from pathlib import Path
from typing import Any


def convert_value(value: str) -> Any:
    """
    Convert text value into an appropriate Python type.

    Examples:
        "OK"      -> "OK"
        "98.5"    -> 98.5
        "100"     -> 100
        "true"    -> True
    """

    value = value.strip()

    if not value:
        return ""

    # Boolean
    if value.lower() == "true":
        return True

    if value.lower() == "false":
        return False

    # Integer
    try:
        return int(value)
    except ValueError:
        pass

    # Float
    try:
        return float(value)
    except ValueError:
        pass

    return value


def parse_line(line: str) -> tuple[str, Any] | None:
    """
    Parse a single line.

    Expected mock format:

        key=value

    Example:

        result=OK
        score=98.5
    """

    line = line.strip()

    # Ignore empty lines
    if not line:
        return None

    # Ignore comments
    if line.startswith("#"):
        return None

    if "=" not in line:
        return None

    key, value = line.split("=", 1)

    key = key.strip()

    if key in {
        "inspection_id",
        "machine_id",
        "camera_id",
    }:
        return key, value

    return key, convert_value(value)


def parse_txt(file_path: str | Path) -> dict[str, Any]:
    """
    Parse an IV4 TXT file.

    This is intentionally generic because the real
    Keyence IV4 TXT format has not been received yet.
    """

    file_path = Path(file_path)

    if not file_path.exists():
        raise FileNotFoundError(
            f"TXT file not found: {file_path}"
        )

    if file_path.suffix.lower() != ".txt":
        raise ValueError(
            f"Expected TXT file: {file_path}"
        )

    data: dict[str, Any] = {}

    for line in read_text_any(file_path).splitlines():

        parsed = parse_line(line)

        if parsed is None:
            continue

        key, value = parsed

        data[key] = value

    return data


def read_text_any(file_path: Path) -> str:
    """
    Decode a TXT written by a PLC / sensor / Windows tool.

    Order: UTF-16 (BOM) -> UTF-8 (with/without BOM) -> cp874 (Thai Windows).
    The real IV4 encoding is not confirmed yet, so be tolerant.
    """
    raw = file_path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp874", errors="replace")


def parse_inspection_txt(
    inspection_folder: str | Path,
) -> dict[str, Any]:
    """
    Parse all TXT files belonging to one inspection.

    Example:

        processing/
        └── 006/
            ├── 006.txt
            └── 006_result.txt
    """

    inspection_folder = Path(
        inspection_folder
    )

    if not inspection_folder.exists():
        raise FileNotFoundError(
            f"Inspection folder not found: "
            f"{inspection_folder}"
        )

    txt_files = sorted(
        inspection_folder.glob("*.txt")
    )

    result = {
        "inspection_id": inspection_folder.name,
        "files": {},
    }

    for txt_file in txt_files:

        parsed_data = parse_txt(
            txt_file
        )

        result["files"][
            txt_file.name
        ] = parsed_data

    return result