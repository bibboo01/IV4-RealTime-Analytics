"""Telegram shift notifications: shift maths, events sent once, alerts only while working."""
import json
import threading
from datetime import datetime, timedelta, timezone

import pytest

from app import notify
from app.config import ConfigError, load_settings
from tests.test_metrics import rec, save

DEFAULT = "A,07:00,15:00,11:00,12:00;B,15:00,23:00,19:00,20:00;C,23:00,07:00,03:00,04:00"


def dt(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M")


def test_parse_shifts_and_errors():
    shifts = notify.parse_shifts(DEFAULT)
    assert [s.name for s in shifts] == ["A", "B", "C"] and shifts[0].break_start.hour == 11
    assert notify.parse_shifts("X,08:00,17:00")[0].break_start is None
    for bad in ("", "A,08:00", "A,8am,17:00", "A,08:00,17:00;A,17:00,01:00"):
        with pytest.raises(ConfigError):
            notify.parse_shifts(bad)


def test_current_shift_and_break_including_overnight():
    shifts = notify.parse_shifts(DEFAULT)
    assert notify.current(dt("2026-10-06 09:00"), shifts)[0].name == "A"
    assert notify.current(dt("2026-10-06 15:00"), shifts)[0].name == "B"          # boundary belongs to the next shift
    s, start, end = notify.current(dt("2026-10-07 02:00"), shifts)                # after midnight = shift C of yesterday
    assert (s.name, start, end) == ("C", dt("2026-10-06 23:00"), dt("2026-10-07 07:00"))
    assert notify.in_break(dt("2026-10-06 11:30"), shifts) and not notify.in_break(dt("2026-10-06 12:00"), shifts)
    assert notify.in_break(dt("2026-10-07 03:30"), shifts)                        # break inside the overnight shift
    assert not notify.in_break(dt("2026-10-06 09:00"), shifts)


def test_events_around_has_start_break_end_per_shift():
    ev = [e for e in notify.events_around(dt("2026-10-06 09:00"), notify.parse_shifts(DEFAULT))
          if e.key.startswith("2026-10-06|C")]
    assert [(e.kind, e.when) for e in ev] == [
        ("start", dt("2026-10-06 23:00")), ("break", dt("2026-10-07 03:00")), ("end", dt("2026-10-07 07:00"))]


@pytest.fixture
def worker(tmp_path, repo):
    s = load_settings(base_dir=tmp_path, overrides={"IV4_TELEGRAM_ENABLED": "true", "IV4_TELEGRAM_TOKEN": "t",
                                                    "IV4_TELEGRAM_CHAT_ID": "1", "IV4_SHIFTS": DEFAULT})
    s.log_dir.mkdir(parents=True, exist_ok=True)
    sent = []
    w = notify.NotifyWorker(s, threading.Event(), send=lambda m: (sent.append(m) or (True, "")))
    w.sent = sent
    return w


def test_shift_end_summary_is_sent_once_and_not_after_restart(worker, repo):
    for i in range(6):                                              # 6 inspections in shift A
        save(repo, rec(100 + i, ts="2026-10-06 09:10:00", status="NG" if i == 0 else "OK",
                       tool2=("NG", 12) if i == 0 else (None, 99)))
    worker.tick(repo, dt("2026-10-06 15:01"))
    msgs = [m for m in worker.sent if "สรุปกะ A" in m]
    assert len(msgs) == 1
    assert "ตรวจ 6" in msgs[0] and "NG 1 (16.67%)" in msgs[0] and "IV4-01" in msgs[0]
    assert any("เริ่มกะ B" in m for m in worker.sent)                # shift B just started
    n = len(worker.sent)
    worker.tick(repo, dt("2026-10-06 15:02"))
    assert len(worker.sent) == n                                     # nothing repeated
    again = notify.NotifyWorker(worker.s, threading.Event(), send=worker._send)       # restart: state file remembered
    assert again.due_events(dt("2026-10-06 15:03")) == []


def test_old_events_are_skipped_after_downtime(worker, repo):
    worker.tick(repo, dt("2026-10-06 16:00"))                         # shift A ended 60 min ago: too old
    assert not any("สรุปกะ A" in m for m in worker.sent)


def test_failed_send_is_retried_next_tick(worker, repo):
    online = {"up": False}
    worker._send = lambda m: (True, "") if online["up"] else (False, "offline")
    worker.tick(repo, dt("2026-10-06 07:01"))
    assert worker.stats["last_error"] == "offline" and not worker.sent_keys       # nothing marked as sent
    online["up"] = True
    worker.tick(repo, dt("2026-10-06 07:02"))                                       # retried within 15 minutes
    assert worker.sent_keys and worker.stats["last_error"] is None


def test_break_message_reports_progress(worker, repo):
    save(repo, rec(1, ts="2026-10-06 09:10:00"))
    worker.tick(repo, dt("2026-10-06 11:01"))
    assert any("พักระหว่างกะ A" in m and "ตรวจ 1" in m for m in worker.sent)


def test_no_data_alert_only_while_working_with_cooldown(worker, repo):
    save(repo, rec(1, ts="2026-10-06 09:10:00"))
    with repo.engine.begin() as c:                                    # last file arrived 30 min ago
        from sqlalchemy import text
        c.execute(text("UPDATE inspection SET created_at = :t"),
                  {"t": datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=30)})
    worker.run_alerts(repo, dt("2026-10-06 09:30"))
    assert any("ไม่มีไฟล์เข้ามา" in m for m in worker.sent)
    n = len(worker.sent)
    worker.run_alerts(repo, dt("2026-10-06 09:40"))                    # cool-down: not again
    assert len(worker.sent) == n
    worker.sent.clear()
    worker.cool.clear()
    worker.run_alerts(repo, dt("2026-10-06 11:30"))                    # break: silent
    assert worker.sent == []


def test_no_data_alert_is_silent_outside_active_hours(worker, repo):
    save(repo, rec(1, ts="2026-10-06 09:10:00"))
    with repo.engine.begin() as c:
        from sqlalchemy import text
        c.execute(text("UPDATE inspection SET created_at = :t"),
                  {"t": datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=30)})
    worker.s.active_hours = frozenset({14, 21})                       # the sensor only sends at 14:00 and 21:00
    worker.run_alerts(repo, dt("2026-10-06 09:30"))
    assert not any("ไม่มีไฟล์เข้ามา" in m for m in worker.sent)
    worker.s.active_hours = frozenset({9})
    worker.run_alerts(repo, dt("2026-10-06 09:30"))
    assert any("ไม่มีไฟล์เข้ามา" in m for m in worker.sent)


def test_alerts_are_one_combined_message_and_missing_needs_a_threshold(worker, repo):
    for i in [0, 1, 2, 5]:                                            # triggers 100..105, 4 received -> 2 missing
        save(repo, rec(100 + i, ts="2026-10-06 09:10:00"))
    (worker.s.log_dir / "health.json").write_text(json.dumps({"incoming_files": 900, "disk_free_gb": 5, "min_free_gb": 20}))
    worker.run_alerts(repo, dt("2026-10-06 09:30"))
    assert len(worker.sent) == 1                                      # several problems, ONE message
    msg = worker.sent[0]
    assert "ไฟล์ค้างรอประมวลผล 900" in msg and "พื้นที่ดิสก์เหลือ 5" in msg and msg.count("•") == 2
    assert "ไฟล์หาย" not in msg                                       # only 2 missing: below IV4_NOTIFY_MISSING_MIN=10


def test_missing_alert_when_enough_files_are_lost_and_only_new_ones_repeat(worker, repo):
    for i in range(0, 40, 4):                                         # 10 received of triggers 100..136 -> 27 missing
        save(repo, rec(100 + i, ts="2026-10-06 09:10:00"))
    worker.run_alerts(repo, dt("2026-10-06 09:30"))
    assert len(worker.sent) == 1 and "ไฟล์หาย 27 ชุด" in worker.sent[0]
    worker.cool.clear()                                               # even without the cool-down: nothing new lost
    worker.run_alerts(repo, dt("2026-10-06 09:45"))
    assert len(worker.sent) == 1


def test_same_alert_waits_for_the_cooldown(worker, repo):
    (worker.s.log_dir / "health.json").write_text(json.dumps({"incoming_files": 900}))
    worker.run_alerts(repo, dt("2026-10-06 09:30"))
    worker.run_alerts(repo, dt("2026-10-06 10:00"))                    # 30 min later: still inside the 60 min cool-down
    assert len(worker.sent) == 1
    worker.run_alerts(repo, dt("2026-10-06 10:31"))                    # past the cool-down: remind once
    assert len(worker.sent) == 2


def test_send_telegram_ok_and_errors(monkeypatch):
    import io
    import urllib.error

    class Resp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(notify.urllib.request, "urlopen", lambda req, timeout: Resp(b'{"ok": true}'))
    assert notify.send_telegram("TOKEN", "1", "hi") == (True, "")

    def boom(req, timeout):
        raise urllib.error.HTTPError("u", 401, "x", {}, io.BytesIO(b'{"ok":false,"description":"Unauthorized"}'))
    monkeypatch.setattr(notify.urllib.request, "urlopen", boom)
    ok, err = notify.send_telegram("SECRETTOKEN", "1", "hi")
    assert not ok and "401" in err and "SECRETTOKEN" not in err

    def offline(req, timeout):
        raise OSError("down")
    monkeypatch.setattr(notify.urllib.request, "urlopen", offline)
    assert notify.send_telegram("t", "1", "hi")[1].startswith("cannot reach Telegram")


def test_notify_cli_status_and_preflight(tmp_path, capsys):
    from app import cli
    s = load_settings(base_dir=tmp_path, overrides={"IV4_TELEGRAM_ENABLED": "true"})
    assert cli.COMMANDS["notify"]([], s) == 0
    out = capsys.readouterr().out
    assert "Shifts" in out and "A  08:00-16:00" in out and "token MISSING" in out
    res = cli.preflight(s)
    assert any(lvl == cli.ERR and "TELEGRAM_TOKEN" in msg for lvl, msg in res)
    bad = load_settings(base_dir=tmp_path, overrides={"IV4_TELEGRAM_ENABLED": "true", "IV4_SHIFTS": "A,xx"})
    assert cli.COMMANDS["notify"]([], bad) == 2


def test_shipped_default_shifts_start_at_eight(tmp_path):
    s = load_settings(base_dir=tmp_path)
    shifts = notify.parse_shifts(s.shifts)
    assert [(x.name, x.start.hour, x.end.hour) for x in shifts] == [("A", 8, 16), ("B", 16, 0), ("C", 0, 8)]
    assert notify.current(dt("2026-10-06 08:00"), shifts)[0].name == "A"
    assert notify.current(dt("2026-10-06 23:59"), shifts)[0].name == "B"
    s_, start, end = notify.current(dt("2026-10-06 16:30"), shifts)
    assert (s_.name, end) == ("B", dt("2026-10-07 00:00"))            # shift B ends at midnight
    assert notify.current(dt("2026-10-07 00:00"), shifts)[0].name == "C"
    assert notify.in_break(dt("2026-10-06 12:30"), shifts) and notify.in_break(dt("2026-10-06 20:30"), shifts)
    assert notify.in_break(dt("2026-10-07 04:30"), shifts) and not notify.in_break(dt("2026-10-07 05:00"), shifts)
