"""Email alerts over SMTP (standard library only).

A worker inside the agent checks every 30 s and sends ONE mail for everything new:
nothing arriving, files missing this hour, NG% too high, score drift, backlog, disk low, upload/Sheets
errors. The same alert is repeated at most every IV4_EMAIL_COOLDOWN_MIN. A mail also goes out once
after the PC came back from a power cut / restart (see boot.message).

The password is read from .env only and never logged or printed.
Settings: IV4_EMAIL_* in .env.example.   Commands: run mail test | status
"""
from __future__ import annotations

import json
import logging
import smtplib
import socket
import ssl
import threading
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formatdate

from sqlalchemy import text

from app.config import Settings
from app.metrics import summarize

log = logging.getLogger("iv4.mail")

LOOP_SECONDS = 30
TIMEOUT = 15.0


def missing_settings(s: Settings) -> list[str]:
    out = []
    if not s.email_host:
        out.append("IV4_EMAIL_SMTP_HOST")
    if not s.email_to:
        out.append("IV4_EMAIL_TO")
    if not (s.email_from or s.email_user):
        out.append("IV4_EMAIL_FROM (or IV4_EMAIL_USER)")
    if s.email_user and not s.email_password:
        out.append("IV4_EMAIL_PASSWORD")
    if s.email_security not in {"starttls", "ssl", "none"}:
        out.append("IV4_EMAIL_SECURITY must be starttls, ssl or none")
    return out


def _net_error(exc: Exception, s: Settings) -> str:
    """Why the mail could not be sent. Never contains the password."""
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return ("login refused - wrong user/password. Gmail/Outlook need an APP PASSWORD, not the normal one "
                "(and IT may require SMTP AUTH to be enabled)")
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return "the server refused the recipient address(es) - check IV4_EMAIL_TO"
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return "the server refused the sender - IV4_EMAIL_FROM must be an address the account may use"
    if isinstance(exc, (ssl.SSLError, smtplib.SMTPNotSupportedError)):
        return (f"secure connection failed ({type(exc).__name__}) - check IV4_EMAIL_SECURITY: "
                "starttls with port 587, ssl with port 465, none with port 25")
    if isinstance(exc, socket.gaierror):
        return f"DNS cannot find {s.email_host} - check the SMTP host name and the network"
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return (f"no answer from {s.email_host}:{s.email_port} - a firewall is probably blocking this port; "
                "ask IT for the company SMTP relay")
    if isinstance(exc, (ConnectionRefusedError, ConnectionResetError, OSError)):
        return (f"cannot connect to {s.email_host}:{s.email_port} ({type(exc).__name__}) - "
                "port blocked or wrong port; ask IT for the company SMTP relay")
    return f"{type(exc).__name__}: {str(exc)[:120]}"


def send_mail(s: Settings, subject: str, body: str) -> tuple[bool, str]:
    """(ok, error)."""
    miss = missing_settings(s)
    if miss:
        return False, "not set: " + ", ".join(miss)
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = s.email_from or s.email_user
    msg["To"] = ", ".join(s.email_to)
    msg["Date"] = formatdate(localtime=True)
    msg.set_content(body)
    try:
        if s.email_security == "ssl":
            smtp = smtplib.SMTP_SSL(s.email_host, s.email_port, timeout=TIMEOUT, context=ssl.create_default_context())
        else:
            smtp = smtplib.SMTP(s.email_host, s.email_port, timeout=TIMEOUT)
        with smtp:
            smtp.ehlo()
            if s.email_security == "starttls":
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
            if s.email_user:
                smtp.login(s.email_user, s.email_password)
            smtp.send_message(msg)
        return True, ""
    except Exception as exc:  # noqa: BLE001 - offline, DNS, auth, TLS
        return False, _net_error(exc, s)


def _hour(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H")


class MailWorker(threading.Thread):

    def __init__(self, settings: Settings, stop_event: threading.Event, send=None, now_fn=None):
        super().__init__(name="mail", daemon=True)
        self.s = settings
        self.stop_event = stop_event
        self._send = send or (lambda subject, body: send_mail(settings, subject, body))
        self._now = now_fn or datetime.now
        self.state_path = settings.log_dir / "mail_state.json"
        self.cool: dict[str, datetime] = self._load()
        self.missing_hour = ""
        self.missing_alerted = 0
        self._miss_now = 0
        self.stats = {"sent": 0, "last_sent_at": None, "last_error": None}

    # state ---------------------------------------------------------
    def _load(self) -> dict:
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8")).get("cool", {})
            return {k: datetime.fromisoformat(v) for k, v in raw.items()}
        except (OSError, ValueError, AttributeError):
            return {}

    def _save(self, now: datetime) -> None:
        cutoff = now - timedelta(days=2)
        self.cool = {k: v for k, v in self.cool.items() if v >= cutoff}
        try:
            self.state_path.write_text(json.dumps({"cool": {k: v.isoformat() for k, v in self.cool.items()}}),
                                       encoding="utf-8")
        except OSError:
            pass

    def _post(self, subject: str, body: str) -> bool:
        ok, err = self._send(subject, body)
        if ok:
            self.stats["sent"] += 1
            self.stats["last_sent_at"] = self._now().isoformat(timespec="seconds")
            self.stats["last_error"] = None
        else:
            self.stats["last_error"] = err
            log.warning("[MAIL] send failed: %s", err)
        return ok

    def _health(self) -> dict:
        try:
            return json.loads((self.s.log_dir / "health.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    # alerts --------------------------------------------------------
    def find_alerts(self, repo, now: datetime) -> list[tuple[str, str]]:
        """[(key, message)] for everything wrong right now (before the cool-down is applied)."""
        s = self.s
        h = self._health()
        found: list[tuple[str, str]] = []
        with repo.engine.connect() as c:
            last = c.execute(text("SELECT MAX(created_at) FROM inspection")).scalar()
        if last:
            try:
                last_dt = last if isinstance(last, datetime) else datetime.fromisoformat(str(last)[:26])
                idle = (datetime.utcnow() - last_dt).total_seconds() / 60
            except ValueError:
                idle = 0
            if idle >= s.email_no_data_min:
                found.append(("nodata", f"No files arrived for {idle:.0f} minutes (check the sensor, FTP, network cable)."))
        hour = _hour(now)
        rows = list(summarize(repo, hour, _hour(now + timedelta(hours=1)), by="hour", per_sensor=True))
        miss = sum(r.missing or 0 for r in rows)
        if self.missing_hour != hour:
            self.missing_hour, self.missing_alerted = hour, 0
        if miss - self.missing_alerted >= s.email_missing_min:
            found.append(("missing", f"{miss:,} files missing this hour (the sensor counted them, the files never arrived). "
                                     "Check FTP and the trigger interval."))
            self._miss_now = miss
        if s.ng_alert_pct:
            for r in rows:
                if r.total >= s.ng_alert_min and (r.ng_pct or 0) >= s.ng_alert_pct:
                    found.append(("ng", f"NG is {r.ng_pct:.1f}% this hour ({r.fail_count:,} of {r.total:,}), above the "
                                        f"{s.ng_alert_pct:g}% limit. Check the line (material lot, light, lens)."))
                    break
        from app.drift import find_drift
        drifts = find_drift(repo, now, s.drift_pct, s.drift_min)
        if drifts:
            found.append(("drift", "Scores moved away from the 7-day normal (check lens, light, material lot):\n    "
                                   + "\n    ".join(d.describe() for d in drifts[:3])))
        waiting = h.get("incoming_files", 0)
        if waiting > s.email_backlog:
            found.append(("backlog", f"{waiting:,} files are waiting to be processed - the agent cannot keep up."))
        disk, low = h.get("disk_free_gb"), h.get("min_free_gb", 0)
        if disk is not None and disk < low:
            found.append(("disk", f"Free disk is {disk:,.0f} GB (below {low:g} GB); old OK images are being deleted."))
        up = h.get("upload") or {}
        if up.get("enabled") and up.get("last_upload_error"):
            found.append(("upload", f"Google Drive upload error: {str(up['last_upload_error'])[:150]} (run upload)"))
        sh = h.get("sheets") or {}
        if sh.get("enabled") and sh.get("last_error"):
            found.append(("sheets", f"Google Sheets update error: {str(sh['last_error'])[:150]}"))
        return found

    def run_alerts(self, repo, now: datetime) -> None:
        """One mail for everything new; the same alert is repeated at most every cool-down."""
        wait = timedelta(minutes=self.s.email_cooldown_min)
        due = [(k, m) for k, m in self.find_alerts(repo, now)
               if not (self.cool.get(k) and now - self.cool[k] < wait)]
        if not due:
            return
        body = "\n\n".join(f"- {m}" for _, m in due) + f"\n\nTime {now:%Y-%m-%d %H:%M}   (IV4 Data Agent)"
        if self._post(f"[IV4] Alert: {len(due)} issue(s) at {now:%H:%M}", body):
            for k, _ in due:
                self.cool[k] = now
            if any(k == "missing" for k, _ in due):
                self.missing_alerted = self._miss_now
            self._save(now)

    def announce_boot(self) -> None:
        """Once per start: tell that the PC came back after a power cut / long stop."""
        from app import boot
        path = self.s.log_dir / "last_boot.json"
        rep = boot._load(path)
        if not rep or rep.get("mailed"):
            return
        if rep.get("clean") is False and rep.get("db_check") is None:
            if (datetime.now(timezone.utc) - boot._t(rep["at"])).total_seconds() < 60:
                return                            # wait for the database check to finish
        kind = "unclean restart" if not rep["clean"] else "restart"
        if self._post(f"[IV4] Machine back ({kind})", boot.message(rep)):
            rep["mailed"] = True
            try:
                path.write_text(json.dumps(rep, indent=2), encoding="utf-8")
            except OSError:
                pass

    def tick(self, repo, now: datetime | None = None) -> None:
        now = now or self._now()
        self.announce_boot()
        self.run_alerts(repo, now)

    def run(self) -> None:
        from app.database.repository import DatabaseRepository

        log.info("[MAIL] email alerts on -> %s", ", ".join(self.s.email_to))
        repo = DatabaseRepository(self.s.database_path)
        try:
            while not self.stop_event.is_set():
                try:
                    self.tick(repo)
                except Exception as exc:  # noqa: BLE001
                    log.exception("[MAIL] tick failed")
                    self.stats["last_error"] = str(exc)[:200]
                self.stop_event.wait(LOOP_SECONDS)
        finally:
            repo.dispose()


# ------------------------------------------------------------------
# `run mail`
# ------------------------------------------------------------------

def run(args: list[str], s: Settings, _running_pid=None) -> int:
    action = args[0] if args and not args[0].startswith("-") else "status"
    miss = missing_settings(s)
    if action == "test":
        if miss:
            print("Set these in .env first: " + ", ".join(miss))
            return 1
        ok, err = send_mail(s, "[IV4] Test email", "IV4 Data Agent: email alerts work.\nทดสอบการแจ้งเตือนทางอีเมลสำเร็จ")
        print(f"Sent to {', '.join(s.email_to)}. Check the inbox (and Spam)." if ok else f"Failed: {err}")
        return 0 if ok else 1
    if action != "status":
        print("Usage: run mail [status|test]")
        return 2
    print(f"Email alerts: {'ON' if s.email_enabled else 'OFF (IV4_EMAIL_ENABLED=false)'}")
    print(f"  server   : {s.email_host or 'MISSING'}:{s.email_port} ({s.email_security})")
    print(f"  login    : {s.email_user or '(none)'}   password {'set' if s.email_password else ('MISSING' if s.email_user else 'not needed')}")
    print(f"  from / to: {s.email_from or s.email_user or 'MISSING'}  ->  {', '.join(s.email_to) or 'MISSING'}")
    print(f"  rules    : no data {s.email_no_data_min} min, missing {s.email_missing_min}/h, backlog {s.email_backlog}, "
          f"NG {s.ng_alert_pct:g}%, drift {s.drift_pct:g}%, repeat every {s.email_cooldown_min} min")
    if miss:
        print("  NOT READY, set: " + ", ".join(miss))
    print("Test: run mail test")
    return 0 if not (s.email_enabled and miss) else 1
