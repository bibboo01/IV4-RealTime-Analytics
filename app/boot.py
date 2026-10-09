"""
Start-up self-check after a power cut or crash.

The agent writes logs/clean_stop.json when it stops normally. At the next start:
  * marker present and newer than the last heartbeat -> normal restart; otherwise the PC / agent died (unclean);
  * downtime = now - (clean stop time, or the last heartbeat);
  * files left in processing/ are recovered (done by the agent) and counted;
  * after an unclean stop the database gets a quick integrity check.
The result is kept in logs/last_boot.json, shown by `run doctor-boot`, and sent to Telegram once (if enabled).
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

AUTO_CHECK_MAX_BYTES = 1_000_000_000      # above this the start-up check would compete with production for the disk
NOTIFY_AFTER_MIN = 5          # a shorter gap is a plain restart (update, run restart): not worth a message


def _load(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _t(iso) -> datetime | None:
    try:
        return datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None


def assess(prev_health: dict | None, marker: dict | None, now: datetime, recovered: int) -> dict:
    beat = _t((prev_health or {}).get("heartbeat_at"))
    stop = _t((marker or {}).get("at"))
    if beat is None and stop is None:
        return {"at": now.isoformat(), "first_start": True, "clean": True, "downtime_min": 0.0,
                "recovered": recovered, "db_check": None, "notified": True}
    clean = stop is not None and (beat is None or stop >= beat.replace(microsecond=0))
    since = stop if clean else beat
    return {"at": now.isoformat(), "first_start": False, "clean": clean,
            "last_alive": since.isoformat(), "downtime_min": round((now - since).total_seconds() / 60, 1),
            "recovered": recovered, "db_check": None,
            "notified": clean and (now - since).total_seconds() / 60 < NOTIFY_AFTER_MIN}   # unclean: always tell


def quick_check(db_path: Path, full: bool = False) -> str:
    import sqlite3
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
        try:
            rows = [r[0] for r in con.execute("PRAGMA integrity_check" if full else "PRAGMA quick_check")]
        finally:
            con.close()
    except Exception as exc:  # noqa: BLE001
        return f"could not check: {str(exc).splitlines()[0][:120]}"
    return "ok" if rows == ["ok"] else "PROBLEM: " + "; ".join(rows[:3])


def record_start(s, prev_health: dict | None, marker: dict | None, recovered: int, log, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    rep = assess(prev_health, marker, now, recovered)
    path = s.log_dir / "last_boot.json"

    def save():
        path.write_text(json.dumps(rep, indent=2), encoding="utf-8")

    save()
    if rep["first_start"]:
        return rep
    log.info("[BOOT] %s restart after %.1f min down, %d file(s) recovered",
             "normal" if rep["clean"] else "UNCLEAN (power cut or crash)", rep["downtime_min"], recovered)
    big = s.database_path.exists() and s.database_path.stat().st_size > AUTO_CHECK_MAX_BYTES
    if not rep["clean"] and big:
        rep["db_check"] = "skipped (database is large - reading it all would slow the disk; run: run doctor-boot)"
        log.info("[BOOT] database check skipped (large database); run doctor-boot when the line is idle")
        save()
    elif not rep["clean"]:
        def check():
            rep["db_check"] = quick_check(s.database_path)
            (log.info if rep["db_check"] == "ok" else log.error)("[BOOT] database check: %s", rep["db_check"])
            save()
        threading.Thread(target=check, name="boot-check", daemon=True).start()
    return rep


def write_clean_stop(s) -> None:
    try:
        (s.log_dir / "clean_stop.json").write_text(json.dumps({"at": datetime.now(timezone.utc).isoformat()}), encoding="utf-8")
    except OSError:
        pass


def take_marker(s) -> dict | None:
    """Read and remove the clean-stop marker (so a later crash is not mistaken for a clean stop)."""
    path = s.log_dir / "clean_stop.json"
    m = _load(path)
    path.unlink(missing_ok=True)
    return m


def message(rep: dict) -> str:
    kind = "ไฟดับ/โปรแกรมหยุดผิดปกติ" if not rep["clean"] else "รีสตาร์ทตามปกติ"
    lines = [f"🔌 เครื่องกลับมาทำงาน ({kind})", f"หยุดไป {rep['downtime_min']:.0f} นาที",
             f"กู้ไฟล์ที่ค้างระหว่างประมวลผล {rep['recovered']:,} ชุด"]
    if rep.get("db_check"):
        lines.append("ฐานข้อมูล: " + ("ปกติ" if rep["db_check"] == "ok" else rep["db_check"]))
    lines.append("ช่วงที่หยุด กล้องส่งไฟล์ไม่ได้ ดูไฟล์หายด้วย run metrics")
    return "\n".join(lines)


def run(args: list[str], s, _running_pid=None) -> int:
    """`run doctor-boot`: what happened at the last start, plus a database check now (--full = slower, deeper)."""
    rep = _load(s.log_dir / "last_boot.json")
    if not rep:
        print("No start recorded yet (it is written each time the agent starts, version 1.11.0+).")
    elif rep.get("first_start"):
        print(f"Last start {rep['at'][:19]}Z: first start on this PC.")
    else:
        print(f"Last start {rep['at'][:19]}Z: {'normal restart' if rep['clean'] else 'UNCLEAN - power cut or crash'}; "
              f"down {rep['downtime_min']:.1f} min; recovered {rep['recovered']:,} file(s); "
              f"database check then: {rep.get('db_check') or 'not run (clean stop)'}")
    waiting = sum(1 for p in s.processing_dir.iterdir() if p.is_dir()) if s.processing_dir.exists() else 0
    print(f"Folders still in processing/: {waiting} (recovered automatically while the agent runs)")
    res = quick_check(s.database_path, full="--full" in args)
    print(f"Database {'integrity' if '--full' in args else 'quick'} check now: {res}")
    if res.startswith("PROBLEM"):
        print("Restore the newest backup: stop the agent, copy backups\\<newest>.db over data\\database\\, start again.")
        return 1
    return 0
