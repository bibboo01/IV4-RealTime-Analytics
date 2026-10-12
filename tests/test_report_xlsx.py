"""run report --excel: workbook with charts, rules-based summary, options and failure messages."""
from datetime import datetime, timedelta

import pytest
from openpyxl import load_workbook
from sqlalchemy import text

from app import report, report_xlsx
from app.database.repository import DatabaseRepository

TODAY = datetime.now().replace(minute=0, second=0, microsecond=0)


def _seed(settings, with_images=False):
    """4 days, 06:00-17:00: Tool 2 NG spikes at 14:00 on two days, one stopped hour, one tiny first hour."""
    repo = DatabaseRepository(settings.database_path)
    with repo.engine.begin() as c:
        for d in range(4, 0, -1):
            day = (TODAY - timedelta(days=d)).replace(hour=0)
            for h in range(6, 18):
                hour = (day + timedelta(hours=h)).strftime("%Y-%m-%d %H")
                total = 12000
                if d == 3 and h == 10:
                    total = 8000                                    # line stopped ~20 min
                spike = h == 14 and d in (3, 1)
                ng = int(total * (0.15 if spike else 0.005))
                tng2 = ng if spike else int(total * 0.003)
                tng1 = int(total * 0.002)
                c.execute(text(
                    "INSERT INTO hourly_stats (hour, program_no, sensor_id, total, pass_count, fail_count, unknown_count,"
                    " time_ms_sum, time_ms_count, time_ms_max, trigger_min, trigger_max, trigger_resets)"
                    " VALUES (:h, 0, 'IV4-01', :t, :ok, :ng, 0, :t * 35, :t, 36, 1, :t, 0)"),
                    {"h": hour, "t": total, "ok": total - ng, "ng": ng})
                for tool, tng, avg in ((1, tng1, 99.4), (2, tng2, 97.0 if spike else 99.6)):
                    c.execute(text(
                        "INSERT INTO hourly_tool_stats (hour, program_no, sensor_id, tool_no, tool_name, count, ng_count,"
                        " value_sum, value_count, value_min, ok_value_min) VALUES (:h, 0, 'IV4-01', :tn, 'AI Differentiate',"
                        " :n, :ng, :vs, :n, 0, 75)"), {"h": hour, "tn": tool, "n": total, "ng": tng, "vs": avg * total})
        for i in range(30):
            c.execute(text(
                "INSERT INTO inspection (uid, inspection_id, timestamp, camera_id, analysis_status, analysis_reason,"
                " created_at, updated_at) VALUES (:u, :i, :ts, 'IV4-01', 'FAIL', :r, :c, :c)"),
                {"u": f"u{i}", "i": f"{i:05d}", "ts": (TODAY - timedelta(days=1)).strftime("%Y-%m-%d 14:%M:00").replace("%M", f"{i:02d}"),
                 "r": f"Tool02:AI Differentiate NG (value={i})", "c": datetime.utcnow()})
    repo.dispose()


def test_workbook_has_sheets_charts_numbers_and_summary(settings, tmp_path):
    _seed(settings)
    out = tmp_path / "r.xlsx"
    assert report.run(["--excel", "--days", "5", "--out", str(out), "--images", "0"], settings) == 0
    wb = load_workbook(out)
    assert wb.sheetnames[:2] == ["สรุป", "กราฟ"] and {"รายชั่วโมง", "รายวัน", "Tool ต่อชั่วโมง", "NG ตามสาเหตุ"} <= set(wb.sheetnames)
    assert len(wb["กราฟ"]._charts) >= 6
    s = wb["สรุป"]
    assert s["A5"].value == 48 * 12000 - 4000 and s["A4"].value == "จำนวนตรวจทั้งหมด"
    text_ = " ".join(str(c.value) for row in s.iter_rows(min_row=8) for c in row if c.value)
    assert "14:00" in text_ and "ซ้ำ" in text_                       # the same clock hour on several days
    assert "Tool" in text_ and "tool 2" in text_                      # NG comes from tool 2
    assert "ยอดต่ำ" in text_ and "ขาด" in text_                        # the stopped hour
    hourly = wb["รายชั่วโมง"]
    assert hourly.cell(2, 2).value == 12000 and 0.0049 < hourly.cell(2, 4).value < 0.0051


def test_english_text_and_single_day_options(settings, tmp_path):
    _seed(settings)
    out = tmp_path / "en.xlsx"
    day = (TODAY - timedelta(days=3)).strftime("%Y-%m-%d")
    assert report.run(["--excel", day, "--lang", "en", "--out", str(out), "--images", "0"], settings) == 0
    wb = load_workbook(out)
    assert wb.sheetnames[0] == "Summary"
    body = " ".join(str(c.value) for row in wb["Summary"].iter_rows(min_row=8) for c in row if c.value)
    assert "Overview" in body and "below the usual volume" in body and "Repeats at the same time" in body
    assert "Less than 2 days" in body                    # one day only
    hours = [r for r in wb["Hourly"].iter_rows(min_row=2, max_col=2, values_only=True) if isinstance(r[0], str) and r[0].endswith(":00")]
    assert len(hours) == 12


def test_samples_are_embedded_only_when_the_picture_exists(settings, tmp_path):
    _seed(settings)
    folder = settings.archive_dir / "x" / "NG" / "14" / "u1"
    folder.mkdir(parents=True)
    from PIL import Image
    Image.new("RGB", (640, 480), "gray").save(folder / "a.jpg")
    repo = DatabaseRepository(settings.database_path)
    with repo.engine.begin() as c:
        c.execute(text("UPDATE inspection SET folder = 'x/NG/14/u1', image_file = 'a.jpg' WHERE uid = 'u29'"))
        c.execute(text("UPDATE inspection SET folder = 'x/NG/14/missing', image_file = 'b.jpg' WHERE uid = 'u28'"))
    repo.dispose()
    out = tmp_path / "i.xlsx"
    assert report.run(["--excel", "--days", "5", "--out", str(out), "--images", "3"], settings) == 0
    wb = load_workbook(out)
    assert len(wb["NG ตัวอย่าง"]._images) == 1


def test_long_ranges_use_lines_and_all_flag_works(settings, tmp_path):
    _seed(settings)
    out = tmp_path / "all.xlsx"
    assert report.run(["--excel", "--all", "--out", str(out), "--images", "0"], settings) == 0
    assert load_workbook(out).sheetnames[0] == "สรุป"
    assert report.run(["--excel", "--from", "2020-01-01", "--to", "2020-01-02", "--out", str(tmp_path / "n.xlsx")], settings) == 1


def test_bad_options_and_missing_library(settings, tmp_path, monkeypatch, capsys):
    assert report.run(["--excel", "--lang", "xx"], settings) == 2
    assert report.run(["--excel", "--days"], settings) == 2
    assert report.run(["--excel", "--nope"], settings) == 2
    assert report.run(["--excel", "2026-13-40"], settings) == 2
    monkeypatch.setattr(report_xlsx, "ensure_openpyxl", lambda: "no internet")
    assert report.run(["--excel"], settings) == 1
    out = capsys.readouterr().out
    assert "Cannot make the Excel report: no internet" in out and "run report" in out


def test_empty_database_gives_a_clear_message(settings, capsys):
    assert report.run(["--excel", "--images", "0"], settings) == 1
    assert "No inspections recorded" in capsys.readouterr().out
