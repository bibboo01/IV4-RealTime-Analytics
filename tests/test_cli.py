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
