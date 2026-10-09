"""
`run update <zip-or-folder>` - install a new version of the program WITHOUT git and without touching your data.

Download the new version as a ZIP (GitHub: Code > Download ZIP), then:

    run update C:\\Users\\me\\Downloads\\IV4-RealTime-Analytics-production-hardening.zip
    run update <zip> --dry-run      show what would change, change nothing
    run update <zip> --restart      safe update: stop, update, self-test, start, watch it for 2 minutes and
                                    GO BACK to the old version by itself if anything fails

Only program files are replaced (app/, scripts/, deploy/, tests/, run.bat, run.sh, README.md,
requirements*.txt, .env.example). NEVER touched: .env, data/, logs/, credentials/, backups/, .venv/.
The old program files are saved to backups/code-<date>.zip first. The agent must be stopped (run stop).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from app.config import BASE_DIR, Settings

CODE_DIRS = ("app", "scripts", "deploy", "tests")
CODE_FILES = ("VERSION", "CHANGELOG.md", "run.bat", "run.sh", "README.md", "requirements.txt", "requirements-dev.txt",
              ".env.example", ".gitignore", ".gitattributes")
SKIP_DIR_NAMES = {"__pycache__", ".pytest_cache"}


def find_root(folder: Path) -> Path | None:
    """The folder that holds app/ and run.bat (a GitHub zip has one wrapper folder)."""
    for cand in [folder, *sorted(p for p in folder.iterdir() if p.is_dir())]:
        if (cand / "app" / "cli.py").exists() and (cand / "run.bat").exists():
            return cand
    return None


def _files(root: Path, name: str) -> list[Path]:
    base = root / name
    if not base.is_dir():
        return []
    return [p for p in base.rglob("*") if p.is_file() and not SKIP_DIR_NAMES & set(p.relative_to(root).parts)]


def plan(src: Path, dst: Path) -> dict:
    """What an update would do: files to add/replace/remove, relative to the install folder."""
    new: list[str] = []
    changed: list[str] = []
    stale: list[str] = []
    for d in CODE_DIRS:
        src_files = {p.relative_to(src).as_posix() for p in _files(src, d)}
        dst_files = {p.relative_to(dst).as_posix() for p in _files(dst, d)}
        for rel in sorted(src_files):
            tgt = dst / rel
            if not tgt.exists():
                new.append(rel)
            elif tgt.read_bytes() != (src / rel).read_bytes():
                changed.append(rel)
        stale += sorted(dst_files - src_files)
    for f in CODE_FILES:
        if (src / f).exists():
            if not (dst / f).exists():
                new.append(f)
            elif (dst / f).read_bytes() != (src / f).read_bytes():
                changed.append(f)
    return {"new": new, "changed": changed, "stale": stale}


def backup_code(dst: Path, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for d in CODE_DIRS:
            for p in _files(dst, d):
                z.write(p, p.relative_to(dst).as_posix())
        for f in CODE_FILES:
            if (dst / f).exists():
                z.write(dst / f, f)
    return out


def apply(src: Path, dst: Path, p: dict) -> None:
    for rel in p["new"] + p["changed"]:
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / rel, target)
    for rel in p["stale"]:
        try:
            (dst / rel).unlink()
        except OSError:
            pass


def restore_backup(zip_path: Path, base: Path) -> bool:
    """Put the program files of a backup zip back (same rules as update: data/.env untouched)."""
    tmp = Path(tempfile.mkdtemp(prefix="iv4-restore-"))
    try:
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(tmp)
        root = find_root(tmp)
        if root is None:
            return False
        apply(root, base, plan(root, base))
        return True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def default_pip(base: Path) -> tuple[bool, str]:
    r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", str(base / "requirements.txt")],
                       capture_output=True, text=True, timeout=900)
    return r.returncode == 0, (r.stderr or r.stdout).strip()[-300:]


SMOKE = ("import importlib, pkgutil, app\n"
         "for m in pkgutil.walk_packages(app.__path__, 'app.'):\n"
         "    if not m.name.endswith('__main__'):\n"
         "        importlib.import_module(m.name)\n"
         "from app.config import load_settings; load_settings()\n"
         "from app.version import read_version; print(read_version())")


def default_smoke(base: Path) -> tuple[bool, str]:
    """The NEW code, in a fresh process: every module imports and the settings load."""
    r = subprocess.run([sys.executable, "-X", "utf8", "-c", SMOKE], cwd=base, capture_output=True, text=True, timeout=180)
    return r.returncode == 0, (r.stderr or r.stdout).strip().splitlines()[-1][:300] if (r.stderr or r.stdout).strip() else ""


def default_wait_healthy(s: Settings, version: str, since: datetime, timeout: int) -> tuple[bool, str]:
    """The restarted agent writes a fresh heartbeat with the NEW version."""
    import json
    import time
    deadline = time.time() + timeout
    why = "no heartbeat from the new agent"
    while time.time() < deadline:
        try:
            h = json.loads((s.log_dir / "health.json").read_text(encoding="utf-8"))
            started = datetime.fromisoformat(h["started_at"])
            beat = datetime.fromisoformat(h["heartbeat_at"])
            now = datetime.now(timezone.utc)
            if started >= since and h.get("version") == version and (now - beat).total_seconds() < 30:
                return True, f"heartbeat {(now - beat).total_seconds():.0f}s old, version {version}"
            why = f"version {h.get('version')} started {started:%H:%M:%S}, heartbeat {(now - beat).total_seconds():.0f}s old"
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(3)
    return False, why


def safe_install(root: Path, base: Path, p: dict, s: Settings, running_pid, new_v: str, hooks: dict | None,
                 timeout: int = 120) -> int:
    """Stop, apply, self-test, start, watch. Anything wrong -> restore the backup and start the old version."""
    hk = {"stop": None, "start": None, "pip": default_pip, "smoke": default_smoke,
          "wait": default_wait_healthy, **(hooks or {})}
    if running_pid():
        print("Stopping the agent...")
        if hk["stop"]() != 0:
            print("The agent did not stop - nothing changed.")
            return 1
    saved = backup_code(base, base / "backups" / f"code-{datetime.now():%Y%m%d-%H%M%S}.zip")
    print(f"Old program saved to {saved}")
    since = datetime.now(timezone.utc)
    need_pip = any(f.startswith("requirements") for f in p["new"] + p["changed"])
    apply(root, base, p)
    problem = None
    if need_pip:
        print("Installing new requirements...")
        ok, msg = hk["pip"](base)
        problem = None if ok else f"installing requirements failed: {msg}"
    if not problem:
        print("Self-test of the new version...")
        ok, msg = hk["smoke"](base)
        problem = None if ok else f"self-test failed: {msg}"
    if not problem:
        print("Starting the new version...")
        hk["start"]()
        print(f"Watching it for up to {timeout}s...")
        ok, msg = hk["wait"](s, new_v, since, timeout)
        print(f"  {msg}")
        problem = None if ok else f"new version is not healthy: {msg}"
    if not problem:
        print(f"Updated to version {new_v} and it is running. Old version kept in {saved.name} (run rollback).")
        return 0
    print(f"PROBLEM: {problem}")
    print("Going back to the old version...")
    if running_pid():
        hk["stop"]()
    if not restore_backup(saved, base):
        print(f"Could not restore automatically. Run: run rollback {saved}")
        return 1
    hk["start"]()
    print(f"ROLLED BACK to the old version (backup {saved.name}). Send logs\\agent.log if you want it investigated.")
    return 1


def run(args: list[str], s: Settings, running_pid, base: Path | None = None, hooks: dict | None = None,
        timeout: int = 120) -> int:
    base = base or BASE_DIR
    safe = "--restart" in args
    dry = "--dry-run" in args
    rest = [a for a in args if not a.startswith("--")]
    if "-h" in args or "--help" in args or len(rest) != 1:
        print(__doc__)
        return 0 if ("-h" in args or "--help" in args) else 2
    source = Path(rest[0]).expanduser()
    if not source.exists():
        print(f"Not found: {source}")
        return 2

    pid = running_pid()
    if pid and not dry and not safe:
        print(f"The agent is running (pid {pid}). Stop it first: run stop")
        return 1

    tmp = None
    try:
        if source.is_file():
            if not zipfile.is_zipfile(source):
                print(f"Not a zip file: {source}")
                return 2
            tmp = Path(tempfile.mkdtemp(prefix="iv4-update-"))
            with zipfile.ZipFile(source) as z:
                z.extractall(tmp)
            folder = tmp
        else:
            folder = source
        root = find_root(folder)
        if root is None:
            print("This does not look like the IV4 Data Agent (no app/ and run.bat found inside).")
            return 2
        if root.resolve() == base.resolve():
            print("The update source is the install folder itself - nothing to do.")
            return 2

        from app.version import read_version
        old_v, new_v = read_version(base), read_version(root)
        p = plan(root, base)
        total = len(p["new"]) + len(p["changed"]) + len(p["stale"])
        print(f"Update from: {source}")
        print(f"  version {old_v} -> {new_v}" if old_v != new_v else f"  version {new_v} (same)")
        print(f"  new {len(p['new'])}   changed {len(p['changed'])}   removed (no longer used) {len(p['stale'])}")
        for label, items in (("new", p["new"]), ("changed", p["changed"]), ("removed", p["stale"])):
            for rel in items[:8]:
                print(f"    {label:8}{rel}")
            if len(items) > 8:
                print(f"    {label:8}... and {len(items) - 8} more")
        print("Kept untouched: .env, data/, logs/, credentials/, backups/, .venv/")
        if not total:
            print("Already up to date.")
            return 0
        if dry:
            print("(dry run - nothing changed)")
            return 0

        if safe:
            return safe_install(root, base, p, s, running_pid, new_v, hooks, timeout)
        saved = backup_code(base, base / "backups" / f"code-{datetime.now():%Y%m%d-%H%M%S}.zip")
        print(f"Old program saved to {saved}")
        apply(root, base, p)
        print(f"Updated to version {new_v}. Start again with: run   (new requirements are installed automatically)")
        return 0
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


ROLLBACK_DOC = """run rollback - go back to the program version saved by the last `run update`.

    run rollback               restore the newest backups/code-*.zip
    run rollback --list        list the saved versions
    run rollback <file.zip>    restore a specific backup
    run rollback --dry-run     show what would change, change nothing

Only program files change (same rules as `run update`); .env, data/, logs/, credentials/ are never touched.
The current program is saved first, so running `run rollback` again undoes the rollback.
The agent must be stopped (run stop)."""


def _backups(base: Path) -> list[Path]:
    return sorted((base / "backups").glob("code-*.zip"), key=lambda z: (z.stat().st_mtime_ns, z.name), reverse=True)


def _zip_version(z: Path) -> str:
    try:
        with zipfile.ZipFile(z) as zf:
            return zf.read("VERSION").decode().strip() or "?"
    except (KeyError, OSError, zipfile.BadZipFile):
        return "?"


def rollback(args: list[str], s: Settings, running_pid, base: Path | None = None) -> int:
    base = base or BASE_DIR
    if "-h" in args or "--help" in args:
        print(ROLLBACK_DOC)
        return 0
    dry = "--dry-run" in args
    rest = [a for a in args if not a.startswith("--")]
    found = _backups(base)
    if "--list" in args:
        if not found:
            print("No saved versions yet (they are created by `run update`).")
        for z in found:
            print(f"  {z.name}   version {_zip_version(z)}")
        return 0
    if len(rest) > 1:
        print(ROLLBACK_DOC)
        return 2
    if rest:
        source = Path(rest[0]).expanduser()
    elif found:
        source = found[0]
    else:
        print("No saved versions in backups/ - nothing to roll back to.")
        return 1
    if not source.is_file() or not zipfile.is_zipfile(source):
        print(f"Not a backup zip: {source}")
        return 2
    pid = running_pid()
    if pid and not dry:
        print(f"The agent is running (pid {pid}). Stop it first: run stop")
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="iv4-rollback-"))
    try:
        with zipfile.ZipFile(source) as z:
            z.extractall(tmp)
        root = find_root(tmp)
        if root is None:
            print("This backup does not look like the IV4 Data Agent.")
            return 2
        from app.version import read_version
        old_v, new_v = read_version(base), read_version(root)
        p = plan(root, base)
        total = len(p["new"]) + len(p["changed"]) + len(p["stale"])
        print(f"Rollback to: {source.name}")
        print(f"  version {old_v} -> {new_v}" if old_v != new_v else f"  version {new_v} (same)")
        print(f"  restored {len(p['new']) + len(p['changed'])}   removed (not in that version) {len(p['stale'])}")
        print("Kept untouched: .env, data/, logs/, credentials/, backups/, .venv/")
        if not total:
            print("Already the same as that backup.")
            return 0
        if dry:
            print("(dry run - nothing changed)")
            return 0
        out = base / "backups" / f"code-{datetime.now():%Y%m%d-%H%M%S}.zip"
        if out.exists() or out == source:
            out = out.with_name(out.stem + "-b.zip")
        saved = backup_code(base, out)
        print(f"Current program saved to {saved.name} (run rollback again to undo this)")
        apply(root, base, p)
        print(f"Rolled back to version {new_v}. Start again with: run")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
