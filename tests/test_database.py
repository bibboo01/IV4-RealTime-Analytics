import sqlite3

from app.analysis.analyzer import analyze_inspection
from app.database.repository import DatabaseRepository
from app.parser.inspection_record import build_inspection_record
from tests.conftest import write_inspection


def _record(tmp_path, id_="006", **kw):
    write_inspection(tmp_path / id_, id_, **kw)
    rec = build_inspection_record(tmp_path / id_)
    return rec, analyze_inspection(rec)


def test_create_then_update_same_uid(repo, tmp_path):
    rec, an = _record(tmp_path)
    row, action = repo.save_or_update_inspection(rec, an, uid="006__a", content_hash="h1")
    assert action == "CREATED" and row.id == 1
    row2, action2 = repo.save_or_update_inspection(rec, an, uid="006__a", content_hash="h1")
    assert action2 == "UPDATED" and row2.id == 1
    assert repo.count() == 1


def test_reused_inspection_id_keeps_history(repo, tmp_path):
    rec, an = _record(tmp_path, result="OK")
    repo.save_or_update_inspection(rec, an, uid="001__t1", content_hash="h1")
    rec2, an2 = _record(tmp_path / "b", result="NG")
    repo.save_or_update_inspection(rec2, an2, uid="001__t2", content_hash="h2")
    assert repo.count() == 2
    assert [r.analysis_status for r in repo.list_by_inspection_id("006")] == ["PASS", "FAIL"]
    assert repo.get_by_inspection_id("006").analysis_status == "FAIL"   # latest


def test_identical_files_are_duplicate(repo, tmp_path):
    rec, an = _record(tmp_path)
    repo.save_or_update_inspection(rec, an, uid="a", content_hash="same")
    _, action = repo.save_or_update_inspection(rec, an, uid="b", content_hash="same")
    assert action == "DUPLICATE"
    assert repo.count() == 1


def test_raw_data_and_counts(repo, tmp_path):
    rec, an = _record(tmp_path)
    row, _ = repo.save_or_update_inspection(rec, an, uid="x")
    assert '"camera_id": "CAM_01"' in row.raw_data
    assert repo.count_by_status() == {"PASS": 1}


def test_migrates_v1_database(tmp_path):
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE inspection (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            inspection_id VARCHAR(100) NOT NULL UNIQUE,
            timestamp VARCHAR(50), machine_id VARCHAR(100), camera_id VARCHAR(100),
            result VARCHAR(50), score FLOAT, width FLOAT, height FLOAT,
            defect_count INTEGER, inspection_time_ms INTEGER, confidence FLOAT,
            analysis_status VARCHAR(50), analysis_reason VARCHAR(1000),
            created_at DATETIME NOT NULL);
        CREATE INDEX ix_inspection_inspection_id ON inspection (inspection_id);
        INSERT INTO inspection (inspection_id, result, analysis_status, created_at)
        VALUES ('006','OK','PASS','2026-09-28 15:29:36'), ('007','OK','PASS','2026-09-28 15:39:58');
    """)
    con.commit()
    con.close()

    repo = DatabaseRepository(db)
    try:
        assert repo.count() == 2
        assert repo.get_by_uid("006__legacy").analysis_status == "PASS"
        rec, an = _record(tmp_path, "006")
        _, action = repo.save_or_update_inspection(rec, an, uid="006__new")
        assert action == "CREATED"          # old UNIQUE(inspection_id) is gone
        assert repo.count() == 3
    finally:
        repo.dispose()

    # running again is a no-op
    DatabaseRepository(db).dispose()
