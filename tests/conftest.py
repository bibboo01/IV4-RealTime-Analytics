from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from app.config import load_settings
from app.database.repository import DatabaseRepository

MOCK_DIR = Path(__file__).parent / "mock_data"


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, s: float):
        self.t += s


def jpeg_bytes(color=(10, 200, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (32, 24), color).save(buf, "JPEG")
    return buf.getvalue()


def write_inspection(
    folder: Path,
    id_: str,
    result: str = "OK",
    score: float = 97.5,
    defect_count: int = 0,
    image: bytes | None = None,
    stamp: str = "2026-09-28 15:00:15",
):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{id_}.txt").write_text(
        f"inspection_id={id_}\ntimestamp={stamp}\nmachine_id=MACHINE_01\n"
        f"camera_id=CAM_01\nresult={result}\nscore={score}\nwidth=120.1\nheight=45.0\n",
        encoding="utf-8",
    )
    (folder / f"{id_}_result.txt").write_text(
        f"result={result}\ndefect_count={defect_count}\ninspection_time_ms=44\nconfidence=0.978\n",
        encoding="utf-8",
    )
    (folder / f"{id_}.jpg").write_bytes(image if image is not None else jpeg_bytes())


@pytest.fixture
def settings(tmp_path):
    s = load_settings(
        base_dir=tmp_path,
        overrides={
            "IV4_SETTLE_SECONDS": "1.0",
            "IV4_GROUP_TIMEOUT": "60",
            "IV4_SCAN_INTERVAL": "0.1",
        },
    )
    s.ensure_dirs()
    return s


@pytest.fixture
def repo(settings):
    r = DatabaseRepository(settings.database_path)
    yield r
    r.dispose()


@pytest.fixture
def clock():
    return FakeClock()
