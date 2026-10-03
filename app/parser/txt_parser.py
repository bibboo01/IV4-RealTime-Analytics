from __future__ import annotations

import re
from datetime import datetime
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


def parse_txt(
    file_path: str | Path,
    date_format: str = "%d/%m/%Y",
) -> dict[str, Any]:
    """
    Parse an IV4 TXT file. Two formats are supported and detected
    automatically:

    * KEYENCE IV4 result file (real sensor, tab separated)::

        Time and Date<TAB>03/10/2026<TAB>18:19:13
        Program No.<TAB>0
        Trigger No.<TAB>301512
        TIME[ms]<TAB>36
        Total Status<TAB>OK
        Tool01:AI Differentiate<TAB>OK<TAB>100<TAB>

    * ``key=value`` (old mock data / tests)
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

    text = read_text_any(file_path)

    if is_iv4_format(text):
        return parse_iv4_text(text, date_format=date_format)

    data: dict[str, Any] = {}

    for line in text.splitlines():

        parsed = parse_line(line)

        if parsed is None:
            continue

        key, value = parsed

        data[key] = value

    return data


# ------------------------------------------------------------------
# KEYENCE IV4 tab-separated result file
# ------------------------------------------------------------------

_TOOL_RE = re.compile(r"^Tool(\d+)\s*:\s*(.*)$", re.IGNORECASE)

_IV4_HEADER_KEYS = {
    "time and date",
    "program no.",
    "trigger no.",
    "time[ms]",
    "total status",
}


def is_iv4_format(text: str) -> bool:
    for line in text.splitlines():
        if not line.strip():
            continue
        if "\t" in line:
            key = line.split("\t", 1)[0].strip().lower()
            return key in _IV4_HEADER_KEYS or bool(_TOOL_RE.match(line.split("\t", 1)[0].strip()))
        return False
    return False


def _parse_iv4_datetime(date: str, time: str, date_format: str) -> str | None:
    try:
        d = datetime.strptime(f"{date.strip()} {time.strip()}", f"{date_format} %H:%M:%S")
    except ValueError:
        return None
    return d.strftime("%Y-%m-%d %H:%M:%S")


def parse_iv4_text(text: str, date_format: str = "%d/%m/%Y") -> dict[str, Any]:
    """
    Returns a normalized dict::

        {
          "format": "iv4",
          "timestamp": "2026-10-03 18:19:13",
          "program_no": 0,
          "trigger_no": 301512,
          "inspection_time_ms": 36,
          "result": "OK",
          "tools": [{"no": 1, "name": "AI Differentiate", "status": "OK", "value": 100}],
          "extra": {...unknown lines...},
        }
    """
    data: dict[str, Any] = {"format": "iv4", "tools": [], "extra": {}}

    for raw in text.splitlines():
        parts = [p.strip() for p in raw.split("\t")]
        while parts and parts[-1] == "":
            parts.pop()
        if not parts or not parts[0]:
            continue

        key, values = parts[0], parts[1:]
        lkey = key.lower()

        tool = _TOOL_RE.match(key)
        if tool:
            data["tools"].append({
                "no": int(tool.group(1)),
                "name": tool.group(2).strip(),
                "status": values[0].upper() if values else None,
                "value": convert_value(values[1]) if len(values) > 1 else None,
                "extra": [convert_value(v) for v in values[2:]],
            })
        elif lkey == "time and date":
            data["date_raw"] = " ".join(values)
            if len(values) >= 2:
                data["timestamp"] = _parse_iv4_datetime(values[0], values[1], date_format)
        elif lkey == "program no.":
            data["program_no"] = convert_value(values[0]) if values else None
        elif lkey == "trigger no.":
            data["trigger_no"] = convert_value(values[0]) if values else None
        elif lkey == "time[ms]":
            data["inspection_time_ms"] = convert_value(values[0]) if values else None
        elif lkey == "total status":
            data["result"] = values[0].upper() if values else None
        else:
            data["extra"][key] = [convert_value(v) for v in values]

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