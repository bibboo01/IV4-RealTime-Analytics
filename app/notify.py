"""
Telegram notifications by work shift.

Shifts come from IV4_SHIFTS: ``name,start,end[,break_start,break_end]`` separated by ``;``
(24-hour PC clock, a shift may cross midnight)::

    A,07:00,15:00,11:00,12:00;B,15:00,23:00,19:00,20:00;C,23:00,07:00,03:00,04:00

Messages (Thai):
  * shift start   - agent state
  * break start   - progress of the shift so far
  * shift end     - shift summary: total, NG, NG %, Yield, Missing per sensor, worst tools
  * alerts while WORKING (never during the break): nothing arriving, files missing, backlog,
    disk low, upload/Sheets errors - each with a cool-down so the chat is not flooded.

Sent events are remembered in logs/notify_state.json, so a restart does not repeat them.
The agent must be running to send (the worker lives inside it).
"""
from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path

from sqlalchemy import text

from app.config import ConfigError, Settings
from app.metrics import miss_text, summarize, tool_summary

log = logging.getLogger("iv4.notify")

MAX_AGE = timedelta(minutes=15)       # an event older than this is skipped (e.g. PC was off)
LOOP_SECONDS = 30
COOLDOWN = {"nodata": 30, "missing": 15, "backlog": 30, "disk": 60, "upload": 60, "sheets": 60}   # minutes


# ------------------------------------------------------------------
# shifts
# ------------------------------------------------------------------

@dataclass(frozen=True)
class Shift:
    name: str
    start: time
    end: time
    break_start: time | None = None
    break_end: time | None = None


@dataclass(frozen=True)
class Event:
    key: str
    when: datetime
    kind: str                 # start | break | end
    shift: Shift
    start_dt: datetime
    end_dt: datetime


def _t(value: str) -> time:
    try:
        h, m = value.strip().split(":")
        return time(int(h), int(m))
    except (ValueError, TypeError):
        raise ConfigError(f"IV4_SHIFTS: '{value}' is not a time like 07:00") from None


def parse_shifts(spec: str) -> list[Shift]:
    out: list[Shift] = []
    for part in (p.strip() for p in spec.split(";") if p.strip()):
        f = [x.strip() for x in part.split(",")]
        if len(f) not in (3, 5) or not f[0]:
            raise ConfigError(f"IV4_SHIFTS: '{part}' must be name,start,end or name,start,end,break_start,break_end")
        out.append(Shift(f[0], _t(f[1]), _t(f[2]), *( (_t(f[3]), _t(f[4])) if len(f) == 5 else (None, None) )))
    if not out:
        raise ConfigError("IV4_SHIFTS is empty")
    if len({s.name for s in out}) != len(out):
        raise ConfigError("IV4_SHIFTS: shift names must be different")
    return out


def _span(day: datetime, s: Shift) -> tuple[datetime, datetime]:
    start = datetime.combine(day.date(), s.start)
    end = datetime.combine(day.date(), s.end)
    if end <= start:
        end += timedelta(days=1)
    return start, end


def _break_span(s: Shift, start: datetime, end: datetime) -> tuple[datetime, datetime] | None:
    if s.break_start is None or s.break_end is None:
        return None
    b0 = datetime.combine(start.date(), s.break_start)
    if b0 < start:
        b0 += timedelta(days=1)
    b1 = datetime.combine(b0.date(), s.break_end)
    if b1 <= b0:
        b1 += timedelta(days=1)
    return (b0, b1) if start <= b0 and b1 <= end else None


def events_around(now: datetime, shifts: list[Shift]) -> list[Event]:
    """All start/break/end events of the shifts that began yesterday, today or tomorrow."""
    out: list[Event] = []
    for d in (-1, 0, 1):
        day = now + timedelta(days=d)
        for s in shifts:
            start, end = _span(day, s)
            tag = f"{start:%Y-%m-%d}|{s.name}"
            out.append(Event(f"{tag}|start", start, "start", s, start, end))
            b = _break_span(s, start, end)
            if b:
                out.append(Event(f"{tag}|break", b[0], "break", s, start, end))
            out.append(Event(f"{tag}|end", end, "end", s, start, end))
    return sorted(out, key=lambda e: e.when)


def current(now: datetime, shifts: list[Shift]) -> tuple[Shift, datetime, datetime] | None:
    for d in (-1, 0):
        day = now + timedelta(days=d)
        for s in shifts:
            start, end = _span(day, s)
            if start <= now < end:
                return s, start, end
    return None


def in_break(now: datetime, shifts: list[Shift]) -> bool:
    cur = current(now, shifts)
    if not cur:
        return False
    s, start, end = cur
    b = _break_span(s, start, end)
    return bool(b and b[0] <= now < b[1])


# ------------------------------------------------------------------
# summaries
# ------------------------------------------------------------------

def _hour(dt: datetime, up: bool = False) -> str:
    if up and (dt.minute or dt.second):
        dt = dt + timedelta(hours=1)
    return dt.strftime("%Y-%m-%d %H")


def shift_summary(repo, start: datetime, end: datetime) -> dict:
    """Totals per sensor between start and end (hour granularity: whole hours that overlap)."""
    a, b = _hour(start), _hour(end, up=True)
    per: dict[str, dict] = {}
    for r in summarize(repo, a, b, by="hour", per_sensor=True):
        d = per.setdefault(r.sensor_id or "-", dict(total=0, ng=0, ok=0, unk=0, missing=0, known=False, unknown_hours=0))
        d["total"] += r.total
        d["ng"] += r.fail_count
        d["ok"] += r.pass_count
        d["unk"] += r.unknown_count
        if r.missing is not None:
            d["missing"] += r.missing
            d["known"] = True
        else:
            d["unknown_hours"] += 1
    all_ = dict(total=0, ng=0, ok=0, unk=0, missing=0, known=False, unknown_hours=0)
    for d in per.values():
        for k in ("total", "ng", "ok", "unk", "missing", "unknown_hours"):
            all_[k] += d[k]
        all_["known"] = all_["known"] or d["known"]
    tools = sorted(tool_summary(repo, a, b), key=lambda t: -t.ng_count)
    return {"per": per, "all": all_, "tools": [t for t in tools if t.ng_count][:3]}


def _pct(n: int, total: int) -> str:
    return f"{100 * n / total:.2f}%" if total else "-"


def _line(name: str, d: dict) -> str:
    miss = miss_text(d["missing"] if d["known"] else None, d["unknown_hours"])
    return (f"{name}: ตรวจ {d['total']:,} | NG {d['ng']:,} ({_pct(d['ng'], d['total'])}) | "
            f"ผ่าน {_pct(d['ok'], d['total'])} | ไฟล์หาย {miss}")


def build_message(kind: str, shift: Shift, start: datetime, end: datetime, summary: dict | None,
                  now: datetime, healthy: str = "") -> str:
    head = f"กะ {shift.name} ({shift.start:%H:%M}-{shift.end:%H:%M})"
    if kind == "start":
        return f"▶️ เริ่ม{head}\n{start:%Y-%m-%d}" + (f"\n{healthy}" if healthy else "")
    s = summary or {"per": {}, "all": dict(total=0, ng=0, ok=0, unk=0, missing=0, known=False, unknown_hours=0), "tools": []}
    lines = [(f"☕ พักระหว่าง{head}\nยอดสะสมถึงตอนนี้ ({now:%H:%M})" if kind == "break"
              else f"🏁 สรุป{head}\n{start:%Y-%m-%d %H:%M} - {end:%Y-%m-%d %H:%M}")]
    if len(s["per"]) > 1:
        lines.append(_line("รวม", s["all"]))
    for name, d in sorted(s["per"].items()):
        lines.append(_line(name, d))
    if not s["per"]:
        lines.append("ยังไม่มีข้อมูล")
    for t in s["tools"]:
        lines.append(f"Tool{t.tool_no:02d} {t.tool_name or ''}: NG {t.ng_count:,}".rstrip())
    if s["all"]["unknown_hours"]:
        lines.append("* บางชั่วโมงเลขนับของ sensor รีเซ็ต วัดไฟล์หายไม่ได้")
    if end.minute or start.minute:
        lines.append("(นับตามชั่วโมงเต็มที่คาบเกี่ยว)")
    return "\n".join(x for x in lines if x)


# ------------------------------------------------------------------
# telegram
# ------------------------------------------------------------------

def send_telegram(token: str, chat_id: str, message: str, timeout: float = 10.0) -> tuple[bool, str]:
    """(ok, error). The token never appears in the returned text."""
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=json.dumps({"chat_id": chat_id, "text": message, "disable_web_page_preview": True}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace") or "{}")
            return (True, "") if body.get("ok") else (False, str(body.get("description", "unknown error")))
    except urllib.error.HTTPError as exc:
        try:
            desc = json.loads(exc.read().decode("utf-8", "replace")).get("description", "")
        except Exception:  # noqa: BLE001
            desc = ""
        hint = {401: "token is wrong", 400: "chat id is wrong or the bot cannot write there",
                403: "the bot was blocked or is not in the group"}.get(exc.code, "")
        return False, f"Telegram {exc.code}: {desc or hint}".strip()
    except Exception as exc:  # noqa: BLE001 - offline, DNS, timeout
        return False, f"cannot reach Telegram: {type(exc).__name__}"


def find_chat_ids(token: str, timeout: float = 10.0) -> tuple[list[tuple[str, str]], str]:
    """Chats that wrote to the bot recently: [(chat_id, name)], error."""
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getUpdates", timeout=timeout) as r:
            data = json.loads(r.read().decode())
    except Exception as exc:  # noqa: BLE001
        return [], f"cannot reach Telegram: {type(exc).__name__}"
    seen: dict[str, str] = {}
    for u in data.get("result", []):
        msg = u.get("message") or u.get("channel_post") or u.get("my_chat_member") or {}
        chat = msg.get("chat") or {}
        if "id" in chat:
            seen[str(chat["id"])] = chat.get("title") or chat.get("username") or chat.get("first_name") or "?"
    return list(seen.items()), ""


# ------------------------------------------------------------------
# worker
# ------------------------------------------------------------------

class NotifyWorker(threading.Thread):

    def __init__(self, settings: Settings, stop_event: threading.Event, send=None, now_fn=None):
        super().__init__(name="notify", daemon=True)
        self.s = settings
        self.stop_event = stop_event
        self.shifts = parse_shifts(settings.shifts)
        self._send = send or (lambda msg: send_telegram(settings.telegram_token, settings.telegram_chat_id, msg))
        self._now = now_fn or datetime.now
        self.state_path = settings.log_dir / "notify_state.json"
        self.sent_keys: dict[str, str] = self._load()
        self.cool: dict[str, datetime] = {}
        self.last_missing = 0
        self.stats = {"sent": 0, "last_sent_at": None, "last_error": None}

    # state ---------------------------------------------------------
    def _load(self) -> dict:
        try:
            return dict(json.loads(self.state_path.read_text(encoding="utf-8")).get("sent", {}))
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        cutoff = (self._now() - timedelta(days=3)).isoformat()
        self.sent_keys = {k: v for k, v in self.sent_keys.items() if v >= cutoff}
        try:
            self.state_path.write_text(json.dumps({"sent": self.sent_keys}), encoding="utf-8")
        except OSError:
            pass

    def _post(self, message: str) -> bool:
        ok, err = self._send(message)
        if ok:
            self.stats["sent"] += 1
            self.stats["last_sent_at"] = self._now().isoformat(timespec="seconds")
            self.stats["last_error"] = None
        else:
            self.stats["last_error"] = err
            log.warning("[NOTIFY] send failed: %s", err)
        return ok

    # shift events --------------------------------------------------
    def due_events(self, now: datetime) -> list[Event]:
        return [e for e in events_around(now, self.shifts)
                if e.when <= now and now - e.when <= MAX_AGE and e.key not in self.sent_keys]

    def run_events(self, repo, now: datetime) -> None:
        for e in self.due_events(now):
            summary = None
            if e.kind == "break":
                summary = shift_summary(repo, e.start_dt, now)
            elif e.kind == "end":
                summary = shift_summary(repo, e.start_dt, e.end_dt)
            msg = build_message(e.kind, e.shift, e.start_dt, e.end_dt, summary, now, self._health_line())
            if self._post(msg):
                self.sent_keys[e.key] = now.isoformat()
                self._save()

    def _health_line(self) -> str:
        h = self._health()
        if not h:
            return "agent ทำงานอยู่ (ยังไม่มีข้อมูล health)"
        return f"agent ทำงานอยู่ | Disk ว่าง {h.get('disk_free_gb', '-')} GB | ไฟล์ค้าง {h.get('incoming_files', 0)}"

    def _health(self) -> dict:
        try:
            return json.loads((self.s.log_dir / "health.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    # alerts --------------------------------------------------------
    def _alert(self, key: str, message: str, now: datetime) -> None:
        last = self.cool.get(key)
        if last and now - last < timedelta(minutes=COOLDOWN.get(key, 30)):
            return
        if self._post("⚠️ " + message):
            self.cool[key] = now

    def run_alerts(self, repo, now: datetime) -> None:
        cur = current(now, self.shifts)
        if not cur or in_break(now, self.shifts):
            return
        shift, start, _ = cur
        s = self.s
        h = self._health()
        if now - start >= timedelta(minutes=s.notify_no_data_min):
            with repo.engine.connect() as c:
                last = c.execute(text("SELECT MAX(created_at) FROM inspection")).scalar()
            if last:
                try:
                    last_dt = last if isinstance(last, datetime) else datetime.fromisoformat(str(last)[:26])
                    idle = (datetime.utcnow() - last_dt).total_seconds() / 60
                except ValueError:
                    idle = 0
                if idle >= s.notify_no_data_min:
                    self._alert("nodata", f"กะ {shift.name}: ไม่มีไฟล์เข้ามา {idle:.0f} นาทีแล้ว "
                                f"(เช็ก sensor / FTP / สายแลน)", now)
        miss = sum(r.missing or 0 for r in summarize(repo, _hour(now), _hour(now + timedelta(hours=1)),
                                                      by="hour", per_sensor=True))
        if miss > self.last_missing:
            self._alert("missing", f"ชั่วโมงนี้ไฟล์หาย {miss:,} ชุด (sensor นับแล้วแต่ไฟล์ไม่มา) "
                        "เช็ก FTP / trigger interval", now)
        self.last_missing = miss
        waiting = h.get("incoming_files", 0)
        if waiting > s.notify_backlog:
            self._alert("backlog", f"มีไฟล์ค้างรอประมวลผล {waiting:,} ไฟล์ agent ตามไม่ทัน", now)
        disk, low = h.get("disk_free_gb"), h.get("min_free_gb", 0)
        if disk is not None and disk < low:
            self._alert("disk", f"พื้นที่ดิสก์เหลือ {disk:,.0f} GB (ต่ำกว่า {low:g} GB) กำลังลบรูป OK เก่า", now)
        up = h.get("upload") or {}
        if up.get("enabled") and up.get("last_upload_error"):
            self._alert("upload", f"อัปโหลด Google Drive ผิดพลาด: {up['last_upload_error'][:150]} (run upload)", now)
        sh = h.get("sheets") or {}
        if sh.get("enabled") and sh.get("last_error"):
            self._alert("sheets", f"อัปเดต Google Sheets ผิดพลาด: {sh['last_error'][:150]}", now)

    # loop ----------------------------------------------------------
    def tick(self, repo, now: datetime | None = None) -> None:
        now = now or self._now()
        self.run_events(repo, now)
        self.run_alerts(repo, now)

    def run(self) -> None:
        from app.database.repository import DatabaseRepository

        log.info("[NOTIFY] Telegram worker started, shifts: %s", self.s.shifts)
        repo = DatabaseRepository(self.s.database_path)
        try:
            while not self.stop_event.is_set():
                try:
                    self.tick(repo)
                except Exception as exc:  # noqa: BLE001
                    log.exception("[NOTIFY] tick failed")
                    self.stats["last_error"] = str(exc)[:200]
                self.stop_event.wait(LOOP_SECONDS)
        finally:
            repo.dispose()


# ------------------------------------------------------------------
# `run notify`
# ------------------------------------------------------------------

def run(args: list[str], s: Settings, running_pid) -> int:
    action = args[0] if args and not args[0].startswith("-") else "status"
    try:
        shifts = parse_shifts(s.shifts)
    except ConfigError as exc:
        print(f"Cannot read IV4_SHIFTS: {exc}")
        return 2

    if action == "chatid":
        if not s.telegram_token:
            print("Set IV4_TELEGRAM_TOKEN in .env first, then send any message to your bot (or add it to the group).")
            return 1
        chats, err = find_chat_ids(s.telegram_token)
        if err:
            print(err)
            return 1
        if not chats:
            print("No chat found yet. Open your bot in Telegram, press Start / send 'hi' (in a group: write a message), "
                  "then run this again.")
            return 1
        for cid, name in chats:
            print(f"  IV4_TELEGRAM_CHAT_ID={cid}    ({name})")
        return 0

    if action == "test":
        if not (s.telegram_token and s.telegram_chat_id):
            print("Set IV4_TELEGRAM_TOKEN and IV4_TELEGRAM_CHAT_ID in .env first (find the chat id with: run notify chatid).")
            return 1
        ok, err = send_telegram(s.telegram_token, s.telegram_chat_id, "✅ IV4 Data Agent: ทดสอบการแจ้งเตือน Telegram สำเร็จ")
        print("Sent. Check Telegram." if ok else f"Failed: {err}")
        return 0 if ok else 1

    if action == "now":
        from app.database.repository import DatabaseRepository
        now = datetime.now()
        cur = current(now, shifts)
        if not cur:
            print("Now is outside every shift in IV4_SHIFTS.")
            return 1
        shift, start, end = cur
        repo = DatabaseRepository(s.database_path)
        try:
            msg = build_message("break", shift, start, end, shift_summary(repo, start, now), now).replace("☕ พักระหว่าง", "📊 สถานะ")
        finally:
            repo.dispose()
        print(msg)
        if "--send" in args:
            ok, err = send_telegram(s.telegram_token, s.telegram_chat_id, msg)
            print("Sent." if ok else f"Not sent: {err}")
            return 0 if ok else 1
        return 0

    # status
    now = datetime.now()
    print(f"Telegram    : {'ON' if s.telegram_enabled else 'OFF (IV4_TELEGRAM_ENABLED=false)'}"
          f"   token {'set' if s.telegram_token else 'MISSING'}   chat id {'set' if s.telegram_chat_id else 'MISSING'}")
    print("Shifts      :")
    for sh in shifts:
        brk = f"   break {sh.break_start:%H:%M}-{sh.break_end:%H:%M}" if sh.break_start and sh.break_end else ""
        print(f"  {sh.name}  {sh.start:%H:%M}-{sh.end:%H:%M}{brk}")
    cur = current(now, shifts)
    print(f"Now         : {now:%Y-%m-%d %H:%M}  " + (f"shift {cur[0].name}{' (break)' if in_break(now, shifts) else ''}" if cur else "outside shifts"))
    nxt = [e for e in events_around(now, shifts) if e.when > now][:3]
    for e in nxt:
        print(f"  next: {e.when:%m-%d %H:%M}  {e.kind:5} shift {e.shift.name}")
    print("Test: run notify test    Find chat id: run notify chatid    Preview: run notify now [--send]")
    return 0
