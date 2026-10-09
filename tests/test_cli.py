"""One-command launcher: config parsing, preflight, single-instance lock, commands."""
import json
from datetime import datetime, timezone

import pytest

from app import cli
from app.config import ConfigError, _clean_value, load_settings


@pytest.mark.parametrize("raw,expected", [
    ("1.0        # seconds between scans", "1.0"),
    ("         # empty value with comment", ""),
    ('"D:\\data #1\\in"', "D:\\data #1\\in"),       # quoted keeps '#'
    ("abc#def", "abc#def"),                          # '#' without space is data
    ("%d/%m/%Y", "%d/%m/%Y"),
    ("'IV4 Data Agent'", "IV4 Data Agent"),
])
def test_env_inline_comments(raw, expected):
    assert _clean_value(raw) == expected


def test_shipped_env_example_parses(tmp_path):
    """Regression: copying .env.example to .env used to crash on inline comments."""
    from app.config import BASE_DIR
    (tmp_path / ".env").write_text((BASE_DIR / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")
    s = load_settings(base_dir=tmp_path)
    assert s.scan_interval == 1.0 and s.sensors == ("IV4-01", "IV4-02")
    assert s.score_threshold is None and s.upload_statuses == frozenset({"FAIL", "UNKNOWN"})


def test_bad_number_names_the_setting(tmp_path):
    (tmp_path / ".env").write_text("IV4_GROUP_TIMEOUT=two minutes\n")
    with pytest.raises(ConfigError, match="IV4_GROUP_TIMEOUT='two minutes'"):
        load_settings(base_dir=tmp_path)


def test_main_reports_config_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_settings", lambda: (_ for _ in ()).throw(ConfigError("IV4_X='y' bad")))
    assert cli.main(["check"]) == 2
    assert "Configuration error: IV4_X='y' bad" in capsys.readouterr().out


def test_single_instance_lock(tmp_path):
    a, b = cli.InstanceLock(tmp_path), cli.InstanceLock(tmp_path)
    assert a.acquire()
    assert not b.acquire()                       # second agent refused
    assert b.running_pid() == str(__import__("os").getpid())
    a.release()
    assert b.running_pid() is None
    assert b.acquire()
    b.release()


def test_start_refused_when_already_running(settings, monkeypatch, capsys):
    monkeypatch.setattr(cli, "preflight", lambda s: [])
    holder = cli.InstanceLock(settings.log_dir)
    assert holder.acquire()
    try:
        assert cli.cmd_start([], settings) == 3
        assert "Already running" in capsys.readouterr().out
    finally:
        holder.release()


def test_start_blocked_by_failed_check(settings, monkeypatch, capsys):
    monkeypatch.setattr(cli, "preflight", lambda s: [(cli.ERR, "incoming NOT WRITABLE")])
    assert cli.cmd_start([], settings) == 1
    assert "Not starting" in capsys.readouterr().out


def test_preflight_ok_and_creates_sensor_folders(tmp_path):
    s = load_settings(base_dir=tmp_path, overrides={"IV4_SENSORS": "IV4-01,IV4-02", "IV4_MIN_FREE_GB": "0"})
    results = cli.preflight(s)
    assert not [m for lvl, m in results if lvl == cli.ERR], results
    assert (s.incoming_dir / "IV4-01").is_dir() and (s.incoming_dir / "IV4-02").is_dir()
    assert any("IV4-01 <- IV4-01" in m for _, m in results)


def test_preflight_flags_unwritable_folder(tmp_path):
    s = load_settings(base_dir=tmp_path)
    s.ensure_dirs()
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x")
    s.archive_dir = blocker / "archive"           # parent is a file -> cannot create
    results = cli.preflight(s)
    assert any(lvl == cli.ERR and "archive" in m for lvl, m in results)


def test_status_not_running_and_running(settings, capsys):
    assert cli.cmd_status([], settings) == 1                       # nothing running
    assert "NOT RUNNING" in capsys.readouterr().out

    (settings.log_dir / "health.json").write_text(json.dumps({
        "heartbeat_at": datetime.now(timezone.utc).isoformat(), "processed": 5, "pass": 4, "fail": 1,
        "unknown": 0, "duplicates": 0, "errors": 0, "last_error": None,
        "incoming_by_sensor": {"incoming/ (root)": 0, "IV4-01": 3}, "disk_free_gb": 100, "min_free_gb": 20,
        "upload": {"enabled": False},
    }))
    lock = cli.InstanceLock(settings.log_dir)
    assert lock.acquire()
    try:
        assert cli.cmd_status([], settings) == 0
        out = capsys.readouterr().out
        assert "RUNNING (pid" in out and "IV4-01 3" in out and "(root)" not in out
    finally:
        lock.release()


def test_unknown_command_and_help(capsys):
    assert cli.main(["bogus"]) == 2
    assert cli.main(["help"]) == 0
    assert "run status" in capsys.readouterr().out


def test_bootstrap_hash_changes_with_requirements(tmp_path, monkeypatch):
    import scripts.bootstrap as b
    monkeypatch.setattr(b, "ROOT", tmp_path)
    (tmp_path / "requirements.txt").write_text("watchdog==6.0.0\n")
    h1 = b.requirements_hash(dev=False)
    assert b.requirements_hash(dev=True) != h1
    (tmp_path / "requirements.txt").write_text("watchdog==6.0.1\n")
    assert b.requirements_hash(dev=False) != h1


def _seed_today(settings, now):
    from sqlalchemy import text

    from app.database.repository import DatabaseRepository
    repo = DatabaseRepository(settings.database_path)
    hour = now.strftime("%Y-%m-%d %H")
    with repo.engine.begin() as c:
        for sensor, total, ng, tmin, tmax in (("IV4-01", 1000, 20, 1, 1000), ("IV4-02", 990, 50, 1, 1000)):
            c.execute(text(
                "INSERT INTO hourly_stats (hour, program_no, sensor_id, total, pass_count, fail_count, unknown_count,"
                " time_ms_sum, time_ms_count, time_ms_max, trigger_min, trigger_max, trigger_resets)"
                " VALUES (:h, 0, :s, :t, :ok, :ng, 0, :t * 36, :t, 40, :a, :b, 0)"),
                {"h": hour, "s": sensor, "t": total, "ok": total - ng, "ng": ng, "a": tmin, "b": tmax})
        c.execute(text(
            "INSERT INTO hourly_tool_stats (hour, program_no, sensor_id, tool_no, tool_name, count, ng_count,"
            " value_sum, value_count, value_min, ok_value_min) VALUES (:h, 0, 'IV4-02', 2, 'AI Differentiate',"
            " 990, 50, 95000, 990, 12, 99)"), {"h": hour})
        c.execute(text(
            "INSERT INTO inspection (uid, inspection_id, timestamp, camera_id, analysis_status, analysis_reason,"
            " created_at, updated_at) VALUES ('u1', '00042_x', :ts, 'IV4-02', 'FAIL',"
            " 'Inspection result is NG; Tool02:AI Differentiate NG (value=12)', :c, :c)"),
            {"ts": now.strftime("%Y-%m-%d %H:%M:%S"), "c": datetime.now(timezone.utc).replace(tzinfo=None)})
    repo.dispose()


def test_monitor_alerts_when_ng_share_is_abnormal(settings, capsys):
    import dataclasses
    _seed_today(settings, datetime.now())                          # IV4-01 2.0% NG, IV4-02 5.1% NG
    cli._monitor(["--once", "--no-color"], dataclasses.replace(settings, ng_alert_pct=4.0, ng_alert_min=100))
    out = capsys.readouterr().out
    assert "IV4-02: NG 5.1% this hour" in out and "IV4-01: NG" not in out
    cli._monitor(["--once", "--no-color"], dataclasses.replace(settings, ng_alert_pct=4.0, ng_alert_min=5000))
    assert "NG 5.1% this hour" not in capsys.readouterr().out      # too few inspections: no alert


def test_monitor_once_empty_and_stopped(settings, capsys):
    assert cli._monitor(["--once", "--no-color"], settings) == 0
    out = capsys.readouterr().out
    assert "STOPPED" in out and "no inspections yet today" in out and "agent is not running" in out


def test_monitor_once_shows_today_tools_ng_and_alerts(settings, capsys):
    now = datetime.now()
    _seed_today(settings, now)
    (settings.log_dir / "health.json").write_text(json.dumps({
        "started_at": datetime.now(timezone.utc).isoformat(),
        "heartbeat_at": datetime.now(timezone.utc).isoformat(), "processed": 1990, "pass": 1920, "fail": 70,
        "unknown": 0, "duplicates": 0, "errors": 0, "last_error": None, "incoming_files": 3,
        "incoming_by_sensor": {"incoming/ (root)": 0, "IV4-01": 1, "IV4-02": 2}, "disk_free_gb": 500,
        "min_free_gb": 20, "upload": {"enabled": False},
    }))
    lock = cli.InstanceLock(settings.log_dir)
    assert lock.acquire()
    try:
        assert cli._monitor(["--once", "--no-color"], settings) == 0
    finally:
        lock.release()
    out = capsys.readouterr().out
    assert "RUNNING" in out and "IV4-01 1, IV4-02 2" in out
    assert "\x1b[" not in out                                   # --once / --no-color: plain text
    today = out.split("TODAY", 1)[1].split("LAST", 1)[0]
    lines = {ln.split()[0]: ln for ln in today.splitlines() if ln.strip().startswith(("IV4-0", "ALL"))}
    assert "1,990" in lines["ALL"] and "70" in lines["ALL"] and "3.52%" in lines["ALL"]
    assert "5.05%" in lines["IV4-02"] and " 10 " in lines["IV4-02"]          # 1000 counted, 990 received
    assert "Tool02 AI Differentiate" in out and "00042_x" in out
    assert "Tool02:AI Differentiate NG (value=12)" in out and "Inspection result is" not in out
    assert "IV4-02: 10 inspections missing this hour" in out


def test_gdrive_logout_forgets_account_files(settings, capsys, monkeypatch):
    import scripts.gdrive_auth as ga
    monkeypatch.setattr(ga, "load_settings", lambda: settings)      # never touch the real credentials/
    settings.gdrive_token.parent.mkdir(parents=True, exist_ok=True)
    for f in ga.state_files(settings):
        f.write_text("{}")
    other = settings.gdrive_token.parent / "client_secret.json"
    other.write_text("{}")
    assert cli.COMMANDS["gdrive-logout"]([], settings) == 0
    assert not any(f.exists() for f in ga.state_files(settings))
    assert other.exists()                                    # the app's own client file is kept
    out = capsys.readouterr().out
    assert "token.json" in out and "sheets_state.json" in out


def test_gdrive_switch_refuses_while_agent_runs(settings, capsys, monkeypatch):
    import scripts.gdrive_auth as ga
    monkeypatch.setattr(ga, "load_settings", lambda: settings)
    settings.gdrive_token.parent.mkdir(parents=True, exist_ok=True)
    settings.gdrive_token.write_text("{}")
    lock = cli.InstanceLock(settings.log_dir)
    assert lock.acquire()
    try:
        assert cli.COMMANDS["gdrive-switch"](["--test"], settings) == 1
    finally:
        lock.release()
    assert settings.gdrive_token.exists()                    # nothing deleted
    assert "Stop it first" in capsys.readouterr().out
    assert ga.state_files(settings)[0] == settings.gdrive_token


def test_stop_when_not_running(tmp_path, capsys):
    s = load_settings(base_dir=tmp_path)
    assert cli.COMMANDS["stop"]([], s) == 0
    assert "not running" in capsys.readouterr().out


def test_stop_asks_running_agent_to_exit(tmp_path, capsys):
    """`run stop` drops logs/stop.flag; the agent loop sees it and releases the lock."""
    import threading, time
    from app.ingestion.watcher import STOP_FLAG
    s = load_settings(base_dir=tmp_path)
    lock = cli.InstanceLock(s.log_dir)
    assert lock.acquire()

    def fake_agent():
        while not (s.log_dir / STOP_FLAG).exists():
            time.sleep(0.05)
        lock.release()

    t = threading.Thread(target=fake_agent)
    t.start()
    assert cli.COMMANDS["stop"]([], s) == 0
    t.join(5)
    assert "Stopped." in capsys.readouterr().out


def test_monitor_clock_offset_detects_sensor_clock_difference():
    from datetime import datetime, timedelta
    from app.monitor import _clock_offset
    now = datetime(2026, 10, 6, 11, 14, 0)
    received = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=10)
    # sensor says 04:14 while this PC says 11:14 -> sensor is 7 h behind
    off = _clock_offset(("2026-10-06 04:14:00", received.isoformat(sep=" ")), now)
    assert round(off / 3600, 1) == -7.0
    # same clock -> ~0
    off = _clock_offset(("2026-10-06 11:13:50", received.isoformat(sep=" ")), now)
    assert abs(off) < 2
    # old data is not live -> no verdict
    old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=3)
    assert _clock_offset(("2026-10-06 04:14:00", old.isoformat(sep=" ")), now) is None
    assert _clock_offset(None, now) is None


def test_monitor_live_per_minute_chart(settings, capsys):
    now = datetime.now()
    _seed_today(settings, now)            # one NG row stamped "now"
    assert cli._monitor(["--once", "--no-color", "--live-hours", "2"], settings) == 0
    out = capsys.readouterr().out
    live = out.split(" LIVE", 1)[1].split("TOOLS TODAY", 1)[0]
    rows = [ln for ln in live.splitlines() if ln.strip().startswith(f"{now:%H}:00")]
    assert rows and "NG      1" in rows[0] and "█" in rows[0]           # current hour, one minute cell
    import re
    assert len([ln for ln in live.splitlines() if re.match(r"\s+\d\d:00 ", ln)]) == 2      # --live-hours 2
    assert "LIVE" not in _render_without_live(settings)


def _render_without_live(settings):
    from app import monitor
    from app.database.repository import DatabaseRepository
    repo = DatabaseRepository(settings.database_path)
    try:
        snap = monitor.collect(repo, settings, 8, None, live_hours=0)
    finally:
        repo.dispose()
    return "\n".join(monitor.render(snap, monitor.Style(False), 100))


def test_version_command_and_places(settings, capsys):
    from app.config import BASE_DIR
    shipped = (BASE_DIR / "VERSION").read_text().strip()
    assert shipped.count(".") == 2 and all(p.isdigit() for p in shipped.split("."))
    assert cli.COMMANDS["version"]([], settings) == 0
    assert f"IV4 Data Agent {shipped}" in capsys.readouterr().out
    assert cli._monitor(["--once", "--no-color"], settings) == 0
    assert f"IV4 Data Agent {shipped}" in capsys.readouterr().out
    assert f"## {shipped} " in (BASE_DIR / "CHANGELOG.md").read_text()          # changelog is kept in step


def test_restart_stops_then_starts_again(tmp_path, capsys, monkeypatch):
    s = load_settings(base_dir=tmp_path)
    order = []
    monkeypatch.setattr(cli, "cmd_stop", lambda a, st: order.append(("stop", list(a))) or 0)
    monkeypatch.setattr(cli, "cmd_start", lambda a, st: order.append(("start", list(a))) or 0)
    monkeypatch.setattr(cli, "_boot_entry_exists", lambda: None)
    assert cli.COMMANDS["restart"]([], s) == 0
    assert [o[0] for o in order] == ["stop", "start"]
    assert "--force" in order[0][1] and "--force" not in order[1][1]
    assert cli.COMMANDS["reboot"] is cli.COMMANDS["restart"]


def test_restart_does_not_start_if_stop_failed(tmp_path, monkeypatch):
    s = load_settings(base_dir=tmp_path)
    started = []
    monkeypatch.setattr(cli, "cmd_stop", lambda a, st: 1)
    monkeypatch.setattr(cli, "cmd_start", lambda a, st: started.append(1) or 0)
    assert cli.COMMANDS["restart"]([], s) == 1
    assert started == []


def _seed_tool_hours(settings, now, rows):
    """rows: (hours_ago, count, ng, avg_value)"""
    from datetime import timedelta

    from sqlalchemy import text

    from app.database.repository import DatabaseRepository
    repo = DatabaseRepository(settings.database_path)
    with repo.engine.begin() as c:
        for ago, count, ng, avg in rows:
            c.execute(text(
                "INSERT INTO hourly_tool_stats (hour, program_no, sensor_id, tool_no, tool_name, count, ng_count,"
                " value_sum, value_count, value_min, ok_value_min) VALUES (:h, 0, 'IV4-01', 2, 'AI Differentiate',"
                " :n, :ng, :vs, :n, 10, 90)"),
                {"h": (now - timedelta(hours=ago)).strftime("%Y-%m-%d %H"), "n": count, "ng": ng, "vs": avg * count})
    repo.dispose()


def test_drift_flags_score_drop_and_ng_jump_but_not_thin_data_or_when_off(settings, capsys):
    from app import drift
    from app.database.repository import DatabaseRepository
    now = datetime.now()
    base = [(h, 500, 5, 99.0) for h in range(3, 9)]                # ~3000 normal inspections: NG 1%, score 99
    _seed_tool_hours(settings, now, base + [(0, 400, 40, 80.0), (1, 400, 40, 80.0)])   # now: score 80, NG 10%
    repo = DatabaseRepository(settings.database_path)
    try:
        found = drift.find_drift(repo, now, 10.0, 200)
        assert {d.kind for d in found} == {"score", "ng"}
        assert "average score 80.0 vs normal 99.0" in next(d for d in found if d.kind == "score").describe()
        assert drift.find_drift(repo, now, 0, 200) == []           # off
        assert drift.find_drift(repo, now, 10.0, 5000) == []       # too few recent inspections
    finally:
        repo.dispose()
    assert cli.COMMANDS["drift"]([], settings) == 0
    assert "average score 80.0" in capsys.readouterr().out


def test_drift_quiet_when_nothing_moved(settings, capsys):
    _seed_tool_hours(settings, datetime.now(), [(h, 500, 5, 99.0) for h in range(0, 9)])
    assert cli.COMMANDS["drift"]([], settings) == 0
    assert "No drift" in capsys.readouterr().out


def test_report_writes_html_for_a_day_and_handles_empty_and_bad_dates(settings, tmp_path, capsys):
    from app import report
    now = datetime.now()
    _seed_today(settings, now)
    out = tmp_path / "r.html"
    day = now.strftime("%Y-%m-%d")
    assert report.run([day, "--out", str(out)], settings) == 0
    page = out.read_text(encoding="utf-8")
    assert "Daily Report" in page and "IV4-02" in page and "AI Differentiate" in page and "1,990" in page
    assert report.run(["2020-01-01", "--out", str(out)], settings) == 0
    assert "No inspections recorded" in out.read_text(encoding="utf-8")
    assert report.run(["not-a-date"], settings) == 2
    assert cli.COMMANDS["report"](["-h"], settings) == 0


def test_drift_ng_jump_is_caught_on_a_low_ng_line(settings):
    from app import drift
    from app.database.repository import DatabaseRepository
    now = datetime.now()
    _seed_tool_hours(settings, now, [(h, 2000, 10, 99.0) for h in range(3, 9)]            # normal NG 0.5%
                     + [(0, 1000, 20, 99.0), (1, 1000, 20, 99.0)])                         # now 2.0%: 4x, +1.5 points
    repo = DatabaseRepository(settings.database_path)
    try:
        assert [d.kind for d in drift.find_drift(repo, now, 10.0, 200)] == ["ng"]
    finally:
        repo.dispose()
