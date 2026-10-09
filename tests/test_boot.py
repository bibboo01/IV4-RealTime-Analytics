"""Start-up self-check after a power cut / crash."""
import json
import logging
import threading
from datetime import datetime, timedelta, timezone

from app import boot, notify
from app.config import load_settings
from app.database.repository import DatabaseRepository

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)


def iso(minutes_ago):
    return (NOW - timedelta(minutes=minutes_ago)).isoformat()


def test_assess_first_start_clean_restart_and_power_cut():
    assert boot.assess(None, None, NOW, 0)["first_start"] is True
    clean = boot.assess({"heartbeat_at": iso(3)}, {"at": iso(2.9)}, NOW, 0)
    assert clean["clean"] and clean["downtime_min"] == 2.9 and clean["notified"] is True       # short restart: silent
    long_clean = boot.assess({"heartbeat_at": iso(60)}, {"at": iso(59)}, NOW, 4)
    assert long_clean["clean"] and long_clean["notified"] is False and long_clean["recovered"] == 4
    cut = boot.assess({"heartbeat_at": iso(30)}, None, NOW, 7)
    assert cut["clean"] is False and cut["downtime_min"] == 30.0 and cut["notified"] is False
    blip = boot.assess({"heartbeat_at": iso(1)}, None, NOW, 0)
    assert blip["clean"] is False and blip["notified"] is False                                 # unclean is always announced
    old_marker = boot.assess({"heartbeat_at": iso(10)}, {"at": iso(500)}, NOW, 0)               # marker older than heartbeat
    assert old_marker["clean"] is False


def test_record_start_writes_file_and_checks_database_after_unclean(tmp_path):
    s = load_settings(base_dir=tmp_path)
    s.ensure_dirs()
    DatabaseRepository(s.database_path).dispose()
    rep = boot.record_start(s, {"heartbeat_at": iso(30)}, None, 2, logging.getLogger("t"), now=NOW)
    for t in threading.enumerate():
        if t.name == "boot-check":
            t.join(5)
    saved = json.loads((s.log_dir / "last_boot.json").read_text())
    assert saved["clean"] is False and saved["db_check"] == "ok" and rep["recovered"] == 2


def test_clean_stop_marker_is_consumed(tmp_path):
    s = load_settings(base_dir=tmp_path)
    s.ensure_dirs()
    boot.write_clean_stop(s)
    assert boot.take_marker(s)["at"] and boot.take_marker(s) is None


def test_quick_check_detects_a_damaged_file_and_doctor_command(tmp_path, capsys):
    s = load_settings(base_dir=tmp_path)
    s.ensure_dirs()
    DatabaseRepository(s.database_path).dispose()
    assert boot.run([], s) == 0 and "quick check now: ok" in capsys.readouterr().out
    data = bytearray(s.database_path.read_bytes())
    for i in range(4096 + 100, min(len(data), 4096 * 3), 7):
        data[i] = 0xFF
    s.database_path.write_bytes(bytes(data))
    assert boot.quick_check(s.database_path) != "ok"
    assert boot.run([], s) == 1


def test_notify_announces_a_power_cut_once(tmp_path):
    s = load_settings(base_dir=tmp_path, overrides={"IV4_TELEGRAM_ENABLED": "true", "IV4_TELEGRAM_TOKEN": "t",
                                                    "IV4_TELEGRAM_CHAT_ID": "1"})
    s.ensure_dirs()
    sent = []
    w = notify.NotifyWorker(s, threading.Event(), send=lambda m: (sent.append(m) or (True, "")))
    now = datetime.now(timezone.utc)
    rep = boot.assess({"heartbeat_at": (now - timedelta(minutes=45)).isoformat()}, None, now, 3)
    rep["db_check"] = "ok"
    (s.log_dir / "last_boot.json").write_text(json.dumps(rep))
    w.announce_boot()
    w.announce_boot()
    assert len(sent) == 1 and "ไฟดับ" in sent[0] and "45" in sent[0] and "ปกติ" in sent[0]


def test_large_database_is_not_read_at_startup(tmp_path, monkeypatch):
    s = load_settings(base_dir=tmp_path)
    s.ensure_dirs()
    DatabaseRepository(s.database_path).dispose()
    monkeypatch.setattr(boot, "AUTO_CHECK_MAX_BYTES", 10)
    called = []
    monkeypatch.setattr(boot, "quick_check", lambda *a, **k: called.append(1) or "ok")
    rep = boot.record_start(s, {"heartbeat_at": iso(30)}, None, 0, logging.getLogger("t"), now=NOW)
    assert called == [] and rep["db_check"].startswith("skipped")
