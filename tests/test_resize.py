import io

import pytest
from PIL import Image

from app.config import ConfigError, load_settings
from app.pipeline import shrink_image


def _jpg(path, size):
    Image.new("RGB", size, (200, 30, 30)).save(path, "JPEG")


def test_shrink_replaces_with_smaller_valid_jpeg(tmp_path):
    p = tmp_path / "a.jpg"
    _jpg(p, (1280, 960))
    assert shrink_image(p, (640, 480)) is True
    with Image.open(p) as im:
        assert im.size == (640, 480)
    assert list(tmp_path.glob("*.tmp")) == []


def test_shrink_never_enlarges_and_keeps_aspect(tmp_path):
    small = tmp_path / "s.jpg"
    _jpg(small, (320, 240))
    assert shrink_image(small, (640, 480)) is False
    wide = tmp_path / "w.jpg"
    _jpg(wide, (2000, 500))
    assert shrink_image(wide, (640, 480)) is True
    with Image.open(wide) as im:
        assert im.size == (640, 160)


def test_shrink_keeps_original_when_file_is_broken(tmp_path):
    p = tmp_path / "bad.jpg"
    p.write_bytes(b"not a jpeg")
    assert shrink_image(p, (640, 480)) is False
    assert p.read_bytes() == b"not a jpeg"


def test_setting_parses_and_rejects_nonsense(tmp_path):
    assert load_settings(base_dir=tmp_path, overrides={"IV4_RESIZE_OK": "640x480"}).resize_ok == (640, 480)
    assert load_settings(base_dir=tmp_path, overrides={}).resize_ok is None
    with pytest.raises(ConfigError):
        load_settings(base_dir=tmp_path, overrides={"IV4_RESIZE_OK": "big"})
