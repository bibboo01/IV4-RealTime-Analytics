"""Tests against real KEYENCE IV4-G500CA output (tests/mock_data/iv4)."""
import json
import shutil
import sqlite3

import pytest

from app.analysis.analyzer import FAIL, PASS, analyze_inspection
from app.config import load_settings
from app.database.repository import DatabaseRepository
from app.ingestion.watcher import IV4Agent
from app.parser.inspection_record import build_inspection_record
from app.parser.txt_parser import is_iv4_format, parse_txt
from tests.conftest import MOCK_DIR, jpeg_bytes

IV4 = MOCK_DIR / "iv4"
NG_TXT = IV4 / "SYNTHETIC_NG_00004_03102026_182105.txt"


@pytest.fixture
def iv4_settings(tmp_path):
    """Production defaults (1 image + 1 TXT), fast timing."""
    s = load_settings(
        base_dir=tmp_path,
        overrides={"IV4_SETTLE_SECONDS": "1.0", "IV4_SCAN_INTERVAL": "0.1"},
    )
    s.ensure_dirs()
    return s


def drop(folder, stem, txt_src=None):
    """Copy a real TXT (+ real or generated JPG) into incoming as <stem>.txt/.jpg."""
    txt_src = txt_src or IV4 / f"{stem}.txt"
    shutil.copy(txt_src, folder / f"{stem}.txt")
    real_jpg = IV4 / f"{stem}.jpg"
    if real_jpg.exists():
        shutil.copy(real_jpg, folder / f"{stem}.jpg")
    else:
        (folder / f"{stem}.jpg").write_bytes(jpeg_bytes())


def settle(agent, clock):
    for _ in range(3):
        agent.scan_once()
        clock.advance(1.1)
    agent.scan_once()


# ---------------- parser ----------------

def test_parse_real_file():
    d = parse_txt(IV4 / "00001_03102026_181913.txt")
    assert d["format"] == "iv4"
    assert d["timestamp"] == "2026-10-03 18:19:13"      # DD/MM/YYYY
    assert d["program_no"] == 0
    assert d["trigger_no"] == 301512
    assert d["inspection_time_ms"] == 36
    assert d["result"] == "OK"
    assert [(t["no"], t["name"], t["status"], t["value"]) for t in d["tools"]] == [
        (1, "AI Differentiate", "OK", 100),
        (2, "AI Differentiate", "OK", 100),
    ]


def test_all_real_files_parse_cleanly():
    for f in sorted(IV4.glob("0*.txt")):
        assert is_iv4_format(f.read_text())
        d = parse_txt(f)
        assert d["result"] == "OK" and d["timestamp"] and d["trigger_no"] > 300000
        assert d["extra"] == {}


def test_date_format_is_configurable():
    d = parse_txt(IV4 / "00001_03102026_181913.txt", date_format="%m/%d/%Y")
    assert d["timestamp"] == "2026-03-10 18:19:13"


def test_record_and_analysis_ok(tmp_path):
    shutil.copy(IV4 / "00002_03102026_182003.txt", tmp_path / "00002_03102026_182003.txt")
    rec = build_inspection_record(tmp_path, "00002_03102026_182003", camera_id="IV4-01")
    assert rec.inspection_id == "00002_03102026_182003"
    assert rec.trigger_no == 302513
    assert rec.score == 100.0 and rec.defect_count == 0
    assert rec.camera_id == "IV4-01" and rec.source_format == "iv4"
    assert rec.warnings == []
    assert analyze_inspection(rec).status == PASS


def test_ng_names_the_failing_tool(tmp_path):
    shutil.copy(NG_TXT, tmp_path / "x.txt")
    rec = build_inspection_record(tmp_path, "x")
    assert rec.result == "NG" and rec.defect_count == 1 and rec.score == 12.0
    a = analyze_inspection(rec)
    assert a.status == FAIL
    assert "Tool02:AI Differentiate NG (value=12)" in a.reasons


def test_bad_date_is_warning(tmp_path):
    (tmp_path / "x.txt").write_text("Time and Date\t2026-10-03\t18:00:00\r\nTotal Status\tOK\r\n")
    rec = build_inspection_record(tmp_path, "x")
    assert rec.timestamp is None
    assert any("Time and Date" in w for w in rec.warnings)


# ---------------- end to end with production defaults ----------------

def test_real_files_end_to_end(iv4_settings, clock):
    repo = DatabaseRepository(iv4_settings.database_path)
    try:
        agent = IV4Agent(iv4_settings, repo, clock=clock)
        stems = [f.stem for f in sorted(IV4.glob("0*.txt"))]
        for stem in stems:
            drop(iv4_settings.incoming_dir, stem)
        settle(agent, clock)

        assert repo.count() == 5
        assert list(iv4_settings.incoming_dir.iterdir()) == []
        assert list(iv4_settings.error_dir.iterdir()) == []

        # sequence 00001 appears twice (counter reset) -> both kept
        r1, r2 = (repo.get_by_inspection_id(s) for s in ("00001_03102026_181913", "00001_03102026_181938"))
        assert (r1.trigger_no, r2.trigger_no) == (301512, 302012)
        assert r1.analysis_status == "PASS" and r1.camera_id == "IV4-01"
        assert json.loads(r1.tools_json)[1]["name"] == "AI Differentiate"
    finally:
        repo.dispose()


def test_ng_end_to_end(iv4_settings, clock):
    repo = DatabaseRepository(iv4_settings.database_path)
    try:
        agent = IV4Agent(iv4_settings, repo, clock=clock)
        drop(iv4_settings.incoming_dir, "00004_03102026_182105", NG_TXT)
        settle(agent, clock)
        row = repo.get_by_inspection_id("00004_03102026_182105")
        assert row.analysis_status == "FAIL" and "Tool02" in row.analysis_reason
    finally:
        repo.dispose()


def test_image_without_txt_times_out_to_error(iv4_settings, clock):
    repo = DatabaseRepository(iv4_settings.database_path)
    try:
        agent = IV4Agent(iv4_settings, repo, clock=clock)
        (iv4_settings.incoming_dir / "00009_03102026_190000.jpg").write_bytes(jpeg_bytes())
        settle(agent, clock)
        assert repo.count() == 0
        clock.advance(iv4_settings.group_timeout)
        agent.scan_once()
        assert len(list(iv4_settings.error_dir.iterdir())) == 1
    finally:
        repo.dispose()


# ---------------- DB upgrade v2 -> v3 ----------------

def test_v2_database_gets_new_columns(tmp_path):
    db = tmp_path / "v2.db"
    DatabaseRepository(db).dispose()
    con = sqlite3.connect(db)
    con.execute("DROP INDEX ix_inspection_trigger_no")
    for col in ("program_no", "trigger_no", "tools_json", "source_format"):
        con.execute(f"ALTER TABLE inspection DROP COLUMN {col}")
    con.execute("INSERT INTO inspection (uid, inspection_id, created_at, updated_at) VALUES ('a','a','2026-10-01','2026-10-01')")
    con.commit()
    con.close()

    repo = DatabaseRepository(db)
    try:
        assert repo.count() == 1
        assert repo.get_by_uid("a").trigger_no is None
    finally:
        repo.dispose()
    cols = {r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(inspection)")}
    assert {"program_no", "trigger_no", "tools_json", "source_format"} <= cols


def test_time_offset_moves_hour_and_archive_folder(tmp_path, clock):
    """Sensor clock 2 h ahead: IV4_TIME_OFFSET_HOURS=-2 files the inspection under the real hour."""
    from app.pipeline import shift_timestamp
    assert shift_timestamp("2026-10-03 18:19:13", -2) == "2026-10-03 16:19:13"
    assert shift_timestamp("2026-10-03 01:30:00", -2) == "2026-10-02 23:30:00"       # crosses midnight
    assert shift_timestamp("2026-10-03 18:19:13", 0) == "2026-10-03 18:19:13"
    assert shift_timestamp(None, -2) is None and shift_timestamp("garbage", -2) == "garbage"

    s = load_settings(base_dir=tmp_path, overrides={"IV4_SETTLE_SECONDS": "1.0", "IV4_SCAN_INTERVAL": "0.1",
                                                    "IV4_TIME_OFFSET_HOURS": "-2"})
    s.ensure_dirs()
    repo = DatabaseRepository(s.database_path)
    try:
        agent = IV4Agent(s, repo, clock=clock)
        drop(s.incoming_dir, "00001_03102026_181913")
        settle(agent, clock)
        rec = repo.get_by_inspection_id("00001_03102026_181913")
        assert rec.timestamp == "2026-10-03 16:19:13"
        assert rec.folder.startswith("2026-10-03/OK/16/")
    finally:
        repo.dispose()


def test_timing_and_capacity_in_health(iv4_settings, clock):
    repo = DatabaseRepository(iv4_settings.database_path)
    try:
        agent = IV4Agent(iv4_settings, repo, clock=clock)
        for stem in [f.stem for f in sorted(IV4.glob("0*.txt"))]:
            drop(iv4_settings.incoming_dir, stem)
        settle(agent, clock)
        agent.write_health()
        h = json.loads((iv4_settings.log_dir / "health.json").read_text())
        assert {"scan", "claim", "parse", "db", "archive"} <= set(h["timing_ms"])
        assert h["capacity_per_s"] and h["capacity_per_s"] > 0
    finally:
        repo.dispose()


def test_scan_work_is_bounded(iv4_settings, clock, monkeypatch):
    import app.ingestion.watcher as w

    monkeypatch.setattr(w, "MAX_GROUPS_PER_SCAN", 2)
    repo = DatabaseRepository(iv4_settings.database_path)
    try:
        agent = IV4Agent(iv4_settings, repo, clock=clock)
        for stem in [f.stem for f in sorted(IV4.glob("0*.txt"))]:      # 5 inspections
            drop(iv4_settings.incoming_dir, stem)
        settle(agent, clock)
        assert 0 < repo.count() < 5          # a pass never takes more than the cap
        for _ in range(6):
            agent.scan_once()
            clock.advance(1.1)
        assert repo.count() == 5             # the backlog still drains
    finally:
        repo.dispose()


def test_resize_ok_only_shrinks_pass_images(tmp_path, clock):
    from PIL import Image

    s = load_settings(base_dir=tmp_path, overrides={
        "IV4_SETTLE_SECONDS": "1.0", "IV4_SCAN_INTERVAL": "0.1", "IV4_RESIZE_OK": "16x16"})
    s.ensure_dirs()
    repo = DatabaseRepository(s.database_path)
    try:
        agent = IV4Agent(s, repo, clock=clock)
        for stem in [f.stem for f in sorted(IV4.glob("0*.txt"))]:
            drop(s.incoming_dir, stem)
        drop(s.incoming_dir, "00004_03102026_182105", NG_TXT)
        settle(agent, clock)
        ok_sizes = [max(Image.open(j).size) for j in s.archive_dir.rglob("OK/**/*.jpg")]
        ng_sizes = [max(Image.open(j).size) for d in ("NG", "UNKNOWN") for j in s.archive_dir.rglob(f"{d}/**/*.jpg")]
        assert ok_sizes and all(x <= 16 for x in ok_sizes)
        assert ng_sizes and all(x > 16 for x in ng_sizes)
    finally:
        repo.dispose()


def test_locked_db_does_not_stall_every_batch(tmp_path, clock, monkeypatch):
    s = load_settings(base_dir=tmp_path, overrides={
        "IV4_SETTLE_SECONDS": "1.0", "IV4_SCAN_INTERVAL": "0.1", "IV4_BATCH_SIZE": "1"})
    s.ensure_dirs()
    repo = DatabaseRepository(s.database_path)
    try:
        agent = IV4Agent(s, repo, clock=clock)
        calls = []

        def locked(_items):
            calls.append(1)
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(repo, "save_batch", locked)
        for stem in [f.stem for f in sorted(IV4.glob("0*.txt"))]:
            drop(s.incoming_dir, stem)
        settle(agent, clock)
        assert len(calls) <= 4          # one failed attempt per pass, not one per queued batch
        monkeypatch.undo()
        for _ in range(20):             # once the lock is gone everything is saved (nothing lost)
            agent.recover_processing()
            agent.scan_once()
            clock.advance(1.1)
        assert repo.count() == 5
    finally:
        repo.dispose()
