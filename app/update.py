"""
`run update <zip-or-folder>` - install a new version of the program WITHOUT git and without touching your data.

Download the new version as a ZIP (GitHub: Code > Download ZIP), then:

    run update C:\\Users\\me\\Downloads\\IV4-RealTime-Analytics-production-hardening.zip
    run update <zip> --dry-run      show what would change, change nothing

Only program files are replaced (app/, scripts/, deploy/, tests/, run.bat, run.sh, README.md,
requirements*.txt, .env.example). NEVER touched: .env, data/, logs/, credentials/, backups/, .venv/.
The old program files are saved to backups/code-<date>.zip first. The agent must be stopped (run stop).
"""
from __future__ import annotations

import shutil
import tempfile
import zipfile
from datetime import datetime
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


def run(args: list[str], s: Settings, running_pid, base: Path | None = None) -> int:
    base = base or BASE_DIR
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
    if pid and not dry:
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
