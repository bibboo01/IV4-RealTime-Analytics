from pathlib import Path

from app.analysis.analyzer import FAIL, PASS, UNKNOWN, analyze_inspection
from app.ingestion.matcher import COMPLETE, INVALID, WAITING, get_group_id, validate_group
from app.parser.inspection_record import InspectionRecord, build_inspection_record
from app.parser.txt_parser import parse_txt
from tests.conftest import MOCK_DIR, write_inspection

IMG = frozenset({".jpg", ".jpeg"})
TXT = frozenset({".txt"})


# ---------------- TXT parser ----------------

def test_parse_mock_txt():
    data = parse_txt(MOCK_DIR / "006.txt")
    assert data["inspection_id"] == "006"          # kept as string (leading zero)
    assert data["result"] == "OK"
    assert data["score"] == 98.5
    assert data["machine_id"] == "MACHINE_01"


def test_parse_utf16_and_cp874(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("result=OK\nnote=ทดสอบ\n", encoding="utf-16")
    assert parse_txt(p) == {"result": "OK", "note": "ทดสอบ"}

    p.write_bytes("result=NG\nnote=ชิ้นงาน\n".encode("cp874"))
    assert parse_txt(p) == {"result": "NG", "note": "ชิ้นงาน"}


def test_parse_ignores_comments_and_garbage(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("# header\n\nno equals here\nscore = 12\n", encoding="utf-8")
    assert parse_txt(p) == {"score": 12}


# ---------------- Inspection record ----------------

def test_build_record_merges_result_file(tmp_path):
    write_inspection(tmp_path / "007", "007", score=97.8)
    rec = build_inspection_record(tmp_path / "007")
    assert rec.inspection_id == "007"
    assert rec.score == 97.8
    assert rec.defect_count == 0
    assert rec.confidence == 0.978
    assert rec.source_files == ["007.txt", "007_result.txt"]
    assert rec.raw["camera_id"] == "CAM_01"
    assert rec.warnings == []


def test_build_record_bad_number_is_warning_not_crash(tmp_path):
    f = tmp_path / "x"
    write_inspection(f, "x")
    (f / "x_result.txt").write_text("result=OK\ndefect_count=abc\n", encoding="utf-8")
    rec = build_inspection_record(f)
    assert rec.defect_count is None
    assert any("defect_count" in w for w in rec.warnings)
    assert rec.raw["defect_count"] == "abc"   # raw value preserved


# ---------------- Analyzer ----------------

def _rec(**kw):
    base = dict(inspection_id="1", result="OK", score=95.0, defect_count=0, confidence=0.99)
    base.update(kw)
    return InspectionRecord(**base)


def test_analyzer_pass():
    assert analyze_inspection(_rec()).status == PASS


def test_analyzer_fail_collects_all_reasons():
    a = analyze_inspection(_rec(result="NG", defect_count=2, score=50))
    assert a.status == FAIL
    assert len(a.reasons) == 3


def test_analyzer_missing_result_is_unknown_not_pass():
    assert analyze_inspection(_rec(result=None)).status == UNKNOWN
    assert analyze_inspection(_rec(result="???")).status == UNKNOWN


def test_analyzer_thresholds_configurable():
    r = _rec(score=85, confidence=0.5)
    assert analyze_inspection(r, score_threshold=80).status == PASS
    assert analyze_inspection(r, score_threshold=80, confidence_threshold=0.9).status == FAIL


# ---------------- Matcher ----------------

def test_group_id_keeps_underscores():
    assert get_group_id(Path("007_result.txt")) == "007"
    assert get_group_id(Path("CAM1_0001.jpg")) == "CAM1_0001"
    assert get_group_id(Path("CAM1_0001_result.txt")) == "CAM1_0001"
    assert get_group_id(Path("CAM1_0001_RESULT.TXT")) == "CAM1_0001"


def test_validate_group_states():
    p = Path
    assert validate_group("1", [p("1.jpg")], IMG, TXT).status == WAITING
    assert validate_group("1", [p("1.jpg"), p("1.txt"), p("1_result.txt")], IMG, TXT).status == COMPLETE
    assert validate_group("1", [p("1.jpg"), p("1.jpeg"), p("1.txt")], IMG, TXT).status == INVALID
    assert validate_group("1", [p("1.jpg"), p("1_result.txt"), p("1_result.TXT")], IMG, TXT).status == INVALID
