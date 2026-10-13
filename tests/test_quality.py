"""run quality: finds stops, lost files, NG spikes and bad values; passes clean data."""
from datetime import datetime, timedelta

from sqlalchemy import text

from app import quality
from app.database.repository import DatabaseRepository
from tests.test_report_xlsx import TODAY, _seed


def _repo(settings):
    return DatabaseRepository(settings.database_path)


def test_clean_hours_pass(settings):
    repo = _repo(settings)
    with repo.engine.begin() as c:
        for h in range(10):
            hour = (TODAY - timedelta(hours=h + 1)).strftime("%Y-%m-%d %H")
            c.execute(text(
                "INSERT INTO hourly_stats (hour, program_no, sensor_id, total, pass_count, fail_count, unknown_count,"
                " time_ms_sum, time_ms_count, time_ms_max, trigger_min, trigger_max, trigger_resets)"
                " VALUES (:h, 0, 'S', 12000, 11940, 60, 0, 420000, 12000, 36, 1, 12000, 0)"), {"h": hour})
    found, st = quality.check(repo, TODAY, days=1)
    repo.dispose()
    assert [f for f in found if f.severity != "INFO"] == [], [f.line() for f in found]
    assert st["total"] == 120000


def test_finds_spike_stop_missing_and_bad_values(settings):
    _seed(settings)
    repo = _repo(settings)
    with repo.engine.begin() as c:
        c.execute(text("UPDATE hourly_stats SET trigger_max = trigger_max + 25 WHERE rowid = "
                       "(SELECT MAX(rowid) FROM hourly_stats)"))
        c.execute(text("UPDATE hourly_tool_stats SET value_min = -5 WHERE rowid = (SELECT MIN(rowid) FROM hourly_tool_stats)"))
        c.execute(text("UPDATE hourly_stats SET unknown_count = 3 WHERE rowid = (SELECT MIN(rowid) FROM hourly_stats)"))
    found, _ = quality.check(repo, TODAY, days=5)
    repo.dispose()
    by = {}
    for f in found:
        by.setdefault(f.check, []).append(f)
    assert len(by["ng spike"]) == 2 and all(f.hour.endswith(" 14") for f in by["ng spike"])
    assert any("short" in f.message for f in by["volume"])
    assert by["missing"] and "25" in by["missing"][0].message
    assert by["values"] and by["unknown"]
    assert found[0].severity == "ERR"                                        # worst first


def test_running_hour_missing_is_only_info(settings):
    repo = _repo(settings)
    hour = datetime.now().strftime("%Y-%m-%d %H")
    with repo.engine.begin() as c:
        c.execute(text(
            "INSERT INTO hourly_stats (hour, program_no, sensor_id, total, pass_count, fail_count, unknown_count,"
            " time_ms_sum, time_ms_count, trigger_min, trigger_max, trigger_resets) VALUES (:h, 0, 'S', 100, 100, 0, 0, 3500, 100, 1, 400, 0)"), {"h": hour})
    found, _ = quality.check(repo, datetime.now(), days=1)
    repo.dispose()
    assert [f.severity for f in found if f.check == "missing"] == ["INFO"]


def test_run_exit_codes_options_and_report_file(settings, tmp_path, capsys):
    _seed(settings)
    assert quality.run(["--days", "5"], settings, base=tmp_path) == 1
    out = capsys.readouterr().out
    assert "ng spike" in out and "finding(s) to look at" in out
    assert (tmp_path / "reports").glob("quality-*.txt") and list((tmp_path / "reports").glob("quality-*.txt"))
    assert quality.run(["--days", "0"], settings, base=tmp_path) == 2
    assert quality.run(["--bogus"], settings, base=tmp_path) == 2
    assert quality.run(["--days", "5", "--no-save"], settings, base=tmp_path / "x") == 1
    assert not (tmp_path / "x").exists()


def test_empty_database_is_an_error(settings, tmp_path, capsys):
    assert quality.run(["--no-save"], settings, base=tmp_path) == 1
    assert "no inspections stored" in capsys.readouterr().out
