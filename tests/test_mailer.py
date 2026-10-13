"""Email alerts: SMTP send, one combined mail, cool-down, boot mail, no password leaks."""
import smtplib
import threading
from datetime import datetime, timedelta

from sqlalchemy import text

from app import mailer
from app.config import load_settings
from app.database.repository import DatabaseRepository

NOW = datetime(2026, 10, 13, 10, 30)


def _s(tmp_path, **env):
    base = {"IV4_EMAIL_ENABLED": "true", "IV4_EMAIL_SMTP_HOST": "smtp.test", "IV4_EMAIL_USER": "me@test.com",
            "IV4_EMAIL_PASSWORD": "s3cret-pw", "IV4_EMAIL_TO": "a@test.com; b@test.com"}
    s = load_settings(base_dir=tmp_path, overrides={**base, **env})
    s.ensure_dirs()
    return s


def _seed_ng(s, total=1000, ng=500):
    repo = DatabaseRepository(s.database_path)
    with repo.engine.begin() as c:
        c.execute(text(
            "INSERT INTO hourly_stats (hour, program_no, sensor_id, total, pass_count, fail_count, unknown_count,"
            " time_ms_sum, time_ms_count, time_ms_max, trigger_min, trigger_max, trigger_resets)"
            " VALUES (:h, 0, 'S', :t, :p, :n, 0, 1, 1, 1, 1, :t, 0)"),
            {"h": NOW.strftime("%Y-%m-%d %H"), "t": total, "p": total - ng, "n": ng})
    return repo


class FakeSMTP:
    sent, logins, tls = [], [], []

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def ehlo(self):
        pass

    def starttls(self, context=None):
        FakeSMTP.tls.append(self.host)

    def login(self, user, pw):
        if pw != "s3cret-pw":
            raise smtplib.SMTPAuthenticationError(535, b"bad")
        FakeSMTP.logins.append(user)

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)


def test_settings_and_missing(tmp_path):
    s = _s(tmp_path)
    assert s.email_to == ("a@test.com", "b@test.com") and s.email_port == 587
    assert mailer.missing_settings(s) == []
    assert "IV4_EMAIL_PASSWORD" in mailer.missing_settings(_s(tmp_path, IV4_EMAIL_PASSWORD=""))
    assert "IV4_EMAIL_SMTP_HOST" in mailer.missing_settings(_s(tmp_path, IV4_EMAIL_SMTP_HOST=""))


def test_send_mail_uses_starttls_login_and_all_recipients(tmp_path, monkeypatch):
    FakeSMTP.sent, FakeSMTP.logins, FakeSMTP.tls = [], [], []
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    ok, err = mailer.send_mail(_s(tmp_path), "subj", "body")
    assert ok and not err
    assert FakeSMTP.tls == ["smtp.test"] and FakeSMTP.logins == ["me@test.com"]
    m = FakeSMTP.sent[0]
    assert m["To"] == "a@test.com, b@test.com" and m["From"] == "me@test.com" and m["Subject"] == "subj"


def test_failed_login_explains_and_hides_password(tmp_path, monkeypatch):
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    ok, err = mailer.send_mail(_s(tmp_path, IV4_EMAIL_PASSWORD="wrong-pw"), "s", "b")
    assert not ok and "APP PASSWORD" in err and "wrong-pw" not in err


def test_connection_error_hint(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise TimeoutError("timed out")
    monkeypatch.setattr(smtplib, "SMTP", boom)
    ok, err = mailer.send_mail(_s(tmp_path), "s", "b")
    assert not ok and "firewall" in err and "s3cret-pw" not in err


def test_one_combined_mail_then_cooldown(tmp_path):
    s = _s(tmp_path, IV4_EMAIL_COOLDOWN_MIN="20")
    repo = _seed_ng(s)
    sent = []
    w = mailer.MailWorker(s, threading.Event(), send=lambda sub, body: (sent.append((sub, body)) or (True, "")))
    w.run_alerts(repo, NOW)
    assert len(sent) == 1 and "NG is 50.0%" in sent[0][1]
    w.run_alerts(repo, NOW + timedelta(minutes=5))              # inside the cool-down
    assert len(sent) == 1
    w.run_alerts(repo, NOW + timedelta(minutes=21))             # after it
    assert len(sent) == 2
    w2 = mailer.MailWorker(s, threading.Event(), send=lambda sub, body: (sent.append((sub, body)) or (True, "")),
                           now_fn=lambda: NOW + timedelta(minutes=22))
    w2.run_alerts(repo, NOW + timedelta(minutes=22))            # restart keeps the cool-down
    assert len(sent) == 2
    repo.dispose()


def test_failed_send_is_retried_and_recorded(tmp_path):
    s = _s(tmp_path)
    repo = _seed_ng(s)
    ok = {"v": False}
    w = mailer.MailWorker(s, threading.Event(), send=lambda sub, body: (ok["v"], "" if ok["v"] else "no network"))
    w.run_alerts(repo, NOW)
    assert w.stats["last_error"] == "no network" and not w.cool
    ok["v"] = True
    w.run_alerts(repo, NOW + timedelta(minutes=1))
    assert w.stats["sent"] == 1 and w.stats["last_error"] is None and "ng" in w.cool
    repo.dispose()


def test_quiet_when_all_fine(tmp_path):
    s = _s(tmp_path)
    repo = _seed_ng(s, total=1000, ng=5)
    sent = []
    w = mailer.MailWorker(s, threading.Event(), send=lambda sub, body: (sent.append(sub) or (True, "")))
    w.run_alerts(repo, NOW)
    repo.dispose()
    assert sent == []


def test_backlog_and_disk_from_health(tmp_path):
    import json
    s = _s(tmp_path)
    repo = _seed_ng(s, total=1000, ng=5)
    (s.log_dir / "health.json").write_text(json.dumps(
        {"incoming_files": 900, "disk_free_gb": 3, "min_free_gb": 20}), encoding="utf-8")
    w = mailer.MailWorker(s, threading.Event(), send=lambda *a: (True, ""))
    keys = {k for k, _ in w.find_alerts(repo, NOW)}
    repo.dispose()
    assert {"backlog", "disk"} <= keys


def test_boot_mail_sent_once(tmp_path):
    import json
    s = _s(tmp_path)
    (s.log_dir / "last_boot.json").write_text(json.dumps(
        {"at": "2026-10-13T01:00:00+00:00", "clean": True, "downtime_min": 12, "recovered": 3}), encoding="utf-8")
    sent = []
    w = mailer.MailWorker(s, threading.Event(), send=lambda sub, body: (sent.append(sub) or (True, "")))
    w.announce_boot()
    w.announce_boot()
    assert len(sent) == 1 and "Machine back" in sent[0]


def test_run_status_and_test_commands(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    FakeSMTP.sent = []
    s = _s(tmp_path)
    assert mailer.run(["status"], s) == 0
    out = capsys.readouterr().out
    assert "s3cret-pw" not in out and "password set" in out
    assert mailer.run(["test"], s) == 0 and FakeSMTP.sent
    assert mailer.run(["test"], _s(tmp_path, IV4_EMAIL_SMTP_HOST="")) == 1
