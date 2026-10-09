"""
IV4 Data Agent - one command for everything.

    run                     start the agent (checks first; default)
    run check               preflight checks only
    run clear               delete collected data to start from zero (agent must be stopped; asks first)
    run update <zip>        install a new version from a downloaded ZIP (no git; keeps .env, data, credentials)
    run export-dataset <folder>   copy NG + OK images into a versioned, labelled training set (labels.csv, dataset.json)
    run lineage             which program version + settings produced the results (history of changes)
    run heal                restart the agent if it is alive but stuck (the health-check task runs this; --dry-run)
    run report [date]       one-page daily report as HTML (reports\\report-<date>.html), good for managers
    run drift               is any tool's score drifting from its last 7 days? (early warning before NG rises)
    run rollback            go back to the version saved before the last update (--list, --dry-run)
    run restart             stop the agent and start it again (also: run reboot; the PC itself is not restarted)
    run stop                stop the agent (also the Windows task/service, so it does not restart)
    run notify [test|chatid|now]   Telegram shift notifications: status / send a test / find chat id / preview
    run version             show the program version (see CHANGELOG.md)
    run status              is it running? backlog, errors, today's numbers
    run monitor             live screen: speed, today per sensor, hourly chart, NG (Ctrl+C quits)
    run metrics [...]       production metrics (see: run metrics --help)
    run benchmark [...]     how many sensors can this machine handle
    run gdrive-auth         one-time Google Drive sign-in
    run upload              why isn't it uploading to Google Drive? (diagnosis)
    run gdrive-switch       change Google account (forget the old one, sign in again)
    run gdrive-logout       forget the Google account
    run sheets              publish the Google Sheets dashboard once and print its link
    run backup              online database backup
    run test                run the automated tests
    run production          prepare this Windows PC for production (admin; --dry-run to preview)
    run service install     install as Windows service (admin PowerShell)
    run service uninstall

(`run` = run.bat on Windows, ./run.sh on Linux/macOS; or `python -m app ...`)
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from app.config import BASE_DIR, ConfigError, Settings, load_settings

OK, WARN, ERR = "OK  ", "WARN", "FAIL"


# ------------------------------------------------------------------
# single instance
# ------------------------------------------------------------------

class InstanceLock:
    """OS-level exclusive lock: released automatically if the process dies."""

    def __init__(self, log_dir: Path):
        log_dir.mkdir(parents=True, exist_ok=True)
        self.path = log_dir / "agent.lock"
        self.pid_path = log_dir / "agent.pid"
        self.fh = None

    def acquire(self) -> bool:
        fh = open(self.path, "a+")
        try:
            if os.name == "nt":
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        self.fh = fh
        self.pid_path.write_text(str(os.getpid()))
        return True

    def release(self) -> None:
        if self.fh is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                self.fh.seek(0)
                msvcrt.locking(self.fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fh, fcntl.LOCK_UN)
        finally:
            self.fh.close()
            self.fh = None
            try:
                self.pid_path.unlink()
            except OSError:
                pass

    def running_pid(self) -> str | None:
        """PID of the running agent, or None if nobody holds the lock."""
        if self.acquire():
            self.release()
            return None
        try:
            return self.pid_path.read_text().strip() or "?"
        except OSError:
            return "?"


# ------------------------------------------------------------------
# check
# ------------------------------------------------------------------

def _writable(d: Path) -> str | None:
    try:
        d.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=d, prefix=".iv4_write_test_", delete=True):
            pass
        return None
    except OSError as exc:
        return str(exc)


def _same_volume(a: Path, b: Path) -> bool:
    try:
        return a.stat().st_dev == b.stat().st_dev
    except OSError:
        return True


def _port_open(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def preflight(s: Settings) -> list[tuple[str, str]]:
    """Returns [(level, message)]; any ERR means the agent must not start."""
    out: list[tuple[str, str]] = []
    add = lambda lvl, msg: out.append((lvl, msg))  # noqa: E731

    v = sys.version_info
    add(OK if v >= (3, 10) else ERR, f"Python {v.major}.{v.minor}.{v.micro}")

    for mod, pkg in (("watchdog", "watchdog"), ("sqlalchemy", "SQLAlchemy"), ("PIL", "pillow")):
        try:
            importlib.import_module(mod)
        except ImportError:
            add(ERR, f"missing package {pkg} - run: run (setup installs it) or pip install -r requirements.txt")

    env = BASE_DIR / ".env"
    add(OK if env.exists() else WARN, ".env found" if env.exists() else ".env missing - using defaults")

    try:
        s.ensure_dirs()
    except OSError as exc:
        add(ERR, f"cannot create data folders: {exc}")
    for label, d in (("incoming", s.incoming_dir), ("processing", s.processing_dir),
                     ("archive", s.archive_dir), ("database", s.database_path.parent), ("logs", s.log_dir)):
        err = _writable(d)
        add(ERR if err else OK, f"{label:10} {d}" + (f"  NOT WRITABLE: {err}" if err else ""))

    sensors = [(n, f) for n, f in s.sensor_sources() if f != s.incoming_dir]
    if sensors:
        add(OK, "sensors: " + ", ".join(f"{n} <- {f.name}\\" for n, f in sensors))
    else:
        add(WARN, f"no sensor subfolders - files must arrive directly in incoming\\ (sensor {s.sensor_id}); "
                  "with 2+ sensors set IV4_SENSORS=IV4-01,IV4-02")

    if not _same_volume(s.incoming_dir, s.processing_dir):
        add(WARN, "incoming and processing are on different drives - every file is copied (slow); "
                  "keep them on the same drive")
    if not _same_volume(s.processing_dir, s.archive_dir):
        add(WARN, "archive is on another drive - finished files are copied (OK, but slower)")

    try:
        free = shutil.disk_usage(s.archive_dir).free / 1024**3
        level = OK if free >= s.min_free_gb * 2 else WARN
        add(level, f"disk free {free:.0f} GB on archive drive (guard starts deleting oldest OK images below "
                   f"{s.min_free_gb:.0f} GB)")
    except OSError:
        pass

    try:
        from app.database.repository import DatabaseRepository
        repo = DatabaseRepository(s.database_path)
        n = repo.count()
        repo.dispose()
        add(OK, f"database {s.database_path.name}: {n:,} inspections")
    except Exception as exc:  # noqa: BLE001
        add(ERR, f"database cannot be opened: {exc}")

    if _port_open(21):
        add(OK, "FTP server is listening on port 21")
    else:
        add(WARN, "nothing listening on port 21 - is FileZilla Server running? (sensors cannot send)")

    if not s.upload_enabled:
        add(WARN, "Google Drive upload is OFF (IV4_UPLOAD_ENABLED=false) - nothing is uploaded. See: run upload")
    wants_google = (s.upload_enabled and s.upload_backend == "gdrive") or s.sheets_enabled
    if wants_google and s.gdrive_auth == "oauth":
        if not (s.gdrive_token and s.gdrive_token.exists()):
            add(WARN, "Google Drive/Sheets enabled but not signed in - run: run gdrive-auth")

    if s.telegram_enabled:
        from app.notify import parse_shifts
        try:
            parse_shifts(s.shifts)
        except ConfigError as exc:
            add(ERR, f"Telegram: {exc}")
        if not (s.telegram_token and s.telegram_chat_id):
            add(ERR, "Telegram is ON but IV4_TELEGRAM_TOKEN / IV4_TELEGRAM_CHAT_ID is empty (see: run notify chatid)")

    pid = InstanceLock(s.log_dir).running_pid()
    if pid:
        add(WARN, f"agent already running (pid {pid})")
    return out


def cmd_check(_args, s: Settings) -> int:
    results = preflight(s)
    for lvl, msg in results:
        print(f"  [{lvl}] {msg}")
    errors = sum(1 for lvl, _ in results if lvl == ERR)
    print(f"\n{'READY' if not errors else f'{errors} problem(s) - fix before starting'}")
    return 1 if errors else 0


# ------------------------------------------------------------------
# start
# ------------------------------------------------------------------

def cmd_start(args, s: Settings) -> int:
    print("IV4 Data Agent - preflight")
    results = preflight(s)
    for lvl, msg in results:
        if lvl != OK or "--verbose" in args:
            print(f"  [{lvl}] {msg}")
    if any(lvl == ERR for lvl, _ in results):
        print("Not starting - fix the FAIL items above (details: run check)")
        return 1

    lock = InstanceLock(s.log_dir)
    if not lock.acquire():
        print(f"Already running (pid {lock.running_pid()}). See: run status")
        return 3
    try:
        from app.ingestion.watcher import main as agent_main
        agent_main()
    finally:
        lock.release()
    return 0


# ------------------------------------------------------------------
# status
# ------------------------------------------------------------------

def cmd_status(_args, s: Settings) -> int:
    pid = InstanceLock(s.log_dir).running_pid()
    health_path = s.log_dir / "health.json"
    health = None
    if health_path.exists():
        try:
            health = json.loads(health_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass

    healthy = bool(pid)
    from app.version import read_version
    print(f"Version    : {read_version()}")
    print(f"Agent      : {'RUNNING (pid ' + pid + ')' if pid else 'NOT RUNNING'}")
    if health:
        beat = datetime.fromisoformat(health["heartbeat_at"])
        age = (datetime.now(timezone.utc) - beat).total_seconds()
        stale = pid and age > 60
        healthy = healthy and not stale
        print(f"Heartbeat  : {age:.0f} s ago{'  <-- STALE' if stale else ''}")
        print(f"Since start: processed {health['processed']:,}  (PASS {health['pass']:,} / FAIL {health['fail']:,} "
              f"/ UNKNOWN {health['unknown']:,})  duplicates {health['duplicates']}  errors {health['errors']}")
        if health.get("last_error"):
            print(f"Last error : {health['last_error_at']}  {health['last_error'][:150]}")
        backlog = {k: v for k, v in (health.get("incoming_by_sensor") or {}).items()
                   if v or not k.startswith("incoming/")}
        print("Waiting    : " + (", ".join(f"{k} {v}" for k, v in backlog.items()) or "-"))
        if health.get("disk_free_gb") is not None:
            low = health["disk_free_gb"] < health.get("min_free_gb", 0)
            healthy = healthy and not low
            print(f"Disk free  : {health['disk_free_gb']} GB{'  <-- LOW' if low else ''}")
        up = health.get("upload") or {}
        if up.get("enabled"):
            print(f"Upload     : {up.get('backend')} uploaded {up.get('uploaded', 0)}, pending {up.get('pending', 0)}"
                  + (f", error: {up['last_upload_error'][:100]}" if up.get("last_upload_error") else ""))
        else:
            print("Upload     : OFF (IV4_UPLOAD_ENABLED=false) - run upload for details")

    try:
        from app.database.repository import DatabaseRepository
        from app.metrics import miss_text, summarize
        repo = DatabaseRepository(s.database_path)
        today = datetime.now().strftime("%Y-%m-%d")
        rows = summarize(repo, today, None, by="day", per_sensor=True)
        print(f"\nToday ({today}, sensor clock):")
        if not rows:
            from sqlalchemy import text
            with repo.engine.connect() as c:
                last = c.execute(text("SELECT MAX(hour) FROM hourly_stats")).scalar()
            print("  no inspections yet" + (f" today - latest data is from {last}:00 (check the sensor clock?)"
                                             if last else ""))
        for r in rows:
            miss = miss_text(r.missing, r.unknown_hours)
            print(f"  {r.sensor_id or '-':10} total {r.total:>9,}   NG {r.fail_count:>7,} ({r.ng_pct or 0:.2f}%)"
                  f"   missing {miss:>6}   run {r.active_hours} h")
        repo.dispose()
    except Exception as exc:  # noqa: BLE001
        print(f"\n(database not readable: {exc})")
    return 0 if healthy else 1


def cmd_stop(args, s: Settings) -> int:
    """Ask the agent to finish what it is doing and exit; --force kills it if it does not."""
    import time
    from app.ingestion.watcher import STOP_FLAG

    lock = InstanceLock(s.log_dir)
    flag = s.log_dir / STOP_FLAG
    if os.name == "nt":                 # a boot task/service would otherwise start it again
        for cmd in (["schtasks", "/end", "/tn", "IV4DataAgent"], ["sc", "stop", "IV4DataAgent"]):
            try:
                subprocess.run(cmd, capture_output=True, timeout=30)
            except (OSError, subprocess.SubprocessError):
                pass
    pid = lock.running_pid()
    if not pid:
        flag.unlink(missing_ok=True)
        print("Agent is not running.")
        return 0
    print(f"Stopping agent (pid {pid}) - finishing the current batch...")
    flag.write_text("stop")
    deadline = time.time() + 60
    while time.time() < deadline:
        if not lock.running_pid():
            print("Stopped.")
            return 0
        time.sleep(1)
    flag.unlink(missing_ok=True)
    if "--force" not in args:
        print("Still running after 60 s. Wait a little more, or run: run stop --force")
        return 1
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    else:
        import signal
        os.kill(int(pid), signal.SIGKILL)
    time.sleep(1)
    print("Stopped (forced)." if not lock.running_pid() else "Could not stop it - end the process manually.")
    return 0 if not lock.running_pid() else 1


def _boot_entry_exists() -> str | None:
    """Windows: 'task' / 'service' when `run production` / `run service install` set up a boot entry."""
    if os.name != "nt":
        return None
    for kind, cmd in (("task", ["schtasks", "/query", "/tn", "IV4DataAgent"]),
                      ("service", ["sc", "query", "IV4DataAgent"])):
        try:
            if subprocess.run(cmd, capture_output=True, timeout=30).returncode == 0:
                return kind
        except (OSError, subprocess.SubprocessError):
            pass
    return None


def cmd_restart(args, s: Settings) -> int:
    """Stop the agent (force after 60 s) and start it again. Nothing is lost: files wait in incoming/."""
    rc = cmd_stop([*args, "--force"], s)
    if rc != 0:
        print("Not restarting - the agent did not stop.")
        return rc
    kind = _boot_entry_exists()
    if kind:                                   # let Windows run it in the background, as at boot
        cmd = ["schtasks", "/run", "/tn", "IV4DataAgent"] if kind == "task" else ["sc", "start", "IV4DataAgent"]
        r = subprocess.run(cmd, capture_output=True, text=True)
        print(f"Started again (Windows {kind})." if r.returncode == 0
              else f"Could not start the {kind}: {(r.stdout or r.stderr).strip()[:200]}\nStart it with: run")
        return r.returncode
    print("Starting again in this window...")
    return cmd_start([a for a in args if a != "--force"], s)


def cmd_clear(args, s: Settings) -> int:
    from app import clear
    return clear.run(args, s, InstanceLock(s.log_dir).running_pid)


def cmd_update(args, s: Settings) -> int:
    from app import update
    return update.run(args, s, InstanceLock(s.log_dir).running_pid)


def cmd_rollback(args, s: Settings) -> int:
    from app import update
    return update.rollback(args, s, InstanceLock(s.log_dir).running_pid)


def cmd_drift(args, s: Settings) -> int:
    from app import drift
    return drift.run(args, s)


def cmd_report(args, s: Settings) -> int:
    from app import report
    return report.run(args, s)


def cmd_heal(args, s: Settings) -> int:
    from app import heal
    return heal.run(args, s, InstanceLock(s.log_dir).running_pid)


def cmd_lineage(args, s: Settings) -> int:
    from app import lineage
    return lineage.run(args, s)


def cmd_export_dataset(args, s: Settings) -> int:
    from app import dataset
    return dataset.run(args, s)


def cmd_version(_args, s: Settings) -> int:
    from app.version import read_version
    print(f"IV4 Data Agent {read_version()}")
    return 0


def cmd_notify(args, s: Settings) -> int:
    from app import notify
    return notify.run(args, s, InstanceLock(s.log_dir).running_pid)


def cmd_upload(args, s: Settings) -> int:
    from app import diagnose
    return diagnose.run(args, s, InstanceLock(s.log_dir).running_pid)


def cmd_production(args, s: Settings) -> int:
    from app import production
    return production.main(args, s)


def cmd_sheets(_args, s: Settings) -> int:
    from app.database.repository import DatabaseRepository
    from app.sheets import publish_once
    from app.upload.google_drive import DriveConfigError

    repo = DatabaseRepository(s.database_path)
    try:
        url = publish_once(s, repo)
    except DriveConfigError as exc:
        print(f"Cannot publish: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"Publish failed: {exc}")
        return 1
    finally:
        repo.dispose()
    print(f"Dashboard updated: {url}")
    if not s.sheets_enabled:
        print("To keep it updating while the agent runs, set IV4_SHEETS_ENABLED=true in .env and restart.")
    return 0


def _monitor(args, s: Settings) -> int:
    from app import monitor
    lock = InstanceLock(s.log_dir)
    return monitor.run(args, s, lock.running_pid)


# ------------------------------------------------------------------
# pass-through commands
# ------------------------------------------------------------------

def _run_module_main(module: str, prog: str, args: list[str]) -> int:
    mod = importlib.import_module(module)
    old = sys.argv
    sys.argv = [prog, *args]
    try:
        rv = mod.main()
        return int(rv or 0)
    except SystemExit as e:
        return int(e.code or 0) if not isinstance(e.code, str) else (print(e.code) or 1)
    finally:
        sys.argv = old


def cmd_test(args, _s) -> int:
    if importlib.util.find_spec("pytest") is None:
        print("pytest not installed - run: run test   (the launcher installs dev requirements for 'test')")
        return 1
    return subprocess.call([sys.executable, "-m", "pytest", "-q", *args], cwd=BASE_DIR)


def cmd_service(args, _s) -> int:
    action = args[0] if args else ""
    if os.name != "nt":
        print("Windows service install is Windows-only (deploy/install_service.ps1).")
        return 1
    script = {"install": "install_service.ps1", "uninstall": "uninstall_service.ps1"}.get(action)
    if not script:
        print("usage: run service install [-Nssm C:\\path\\nssm.exe] | run service uninstall")
        return 1
    return subprocess.call(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                            "-File", str(BASE_DIR / "deploy" / script), *args[1:]])


COMMANDS = {
    "start": cmd_start,
    "check": cmd_check,
    "status": cmd_status,
    "stop": cmd_stop,
    "restart": cmd_restart,
    "reboot": cmd_restart,
    "clear": cmd_clear,
    "update": cmd_update,
    "rollback": cmd_rollback,
    "drift": cmd_drift,
    "report": cmd_report,
    "heal": cmd_heal,
    "lineage": cmd_lineage,
    "export-dataset": cmd_export_dataset,
    "version": cmd_version,
    "notify": cmd_notify,
    "monitor": _monitor,
    "sheets": cmd_sheets,
    "metrics": lambda a, s: _run_module_main("scripts.metrics", "run metrics", a),
    "benchmark": lambda a, s: _run_module_main("scripts.benchmark", "run benchmark", a),
    "gdrive-auth": lambda a, s: _run_module_main("scripts.gdrive_auth", "run gdrive-auth", a),
    "gdrive-switch": lambda a, s: _run_module_main("scripts.gdrive_auth", "run gdrive-switch", ["--switch", *a]),
    "gdrive-logout": lambda a, s: _run_module_main("scripts.gdrive_auth", "run gdrive-logout", ["--logout", *a]),
    "backup": lambda a, s: _run_module_main("scripts.backup_db", "run backup", a),
    "test": cmd_test,
    "service": cmd_service,
    "production": cmd_production,
    "upload": cmd_upload,
}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in {"-h", "--help", "help"}:
        print(__doc__)
        return 0
    cmd = argv.pop(0) if argv and not argv[0].startswith("-") else "start"
    if cmd not in COMMANDS:
        print(f"unknown command: {cmd}\n{__doc__}")
        return 2
    os.chdir(BASE_DIR)            # relative paths in .env work from anywhere (double-click, service)
    if str(BASE_DIR) not in sys.path:
        sys.path.insert(0, str(BASE_DIR))
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"Configuration error: {exc}")
        return 2
    try:
        return COMMANDS[cmd](argv, settings)
    except BrokenPipeError:            # output piped into `more` / closed early
        return 0
