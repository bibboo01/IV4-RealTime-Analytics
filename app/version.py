"""Program version: the one-line VERSION file next to run.bat."""
from __future__ import annotations

from pathlib import Path

from app.config import BASE_DIR


def read_version(base: Path | None = None) -> str:
    try:
        return (Path(base or BASE_DIR) / "VERSION").read_text(encoding="utf-8").strip() or "unknown"
    except OSError:
        return "unknown"


__version__ = read_version()
