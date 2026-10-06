"""
`run clear` - wipe collected data to start from zero (agent must be stopped).

Default removes the database and every image/file the agent has stored
(archive, processing, error, uploaded). Never touched: .env, credentials/
(Google sign-in), backups, and anything already uploaded to Google Drive/Sheets.

    run clear                 database + stored images           (asks you to type CLEAR)
    run clear --db-only       database only (images stay on disk)
    run clear --images-only   images/files only (database stays)
    run clear --incoming      also files waiting in the sensor folders (folders are kept)
    run clear --logs          also old log files
    run clear --dry-run       show what would be deleted, delete nothing
    run clear --yes           no question (scripts)
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

from app.config import BASE_DIR, Settings

KEEP_LOG_FILES = {"agent.lock", "agent.pid", "health.json"}
PROTECTED = ("app", "scripts", "deploy", "tests", "credentials", ".git", ".venv")      # never emptied


def _size(path: Path) -> tuple[int, int]:
    """(files, bytes) under path."""
    files = total = 0
    if path.is_file():
        return 1, path.stat().st_size
    for p in path.rglob("*"):
        try:
            if p.is_file():
                files += 1
                total += p.stat().st_size
        except OSError:
            pass
    return files, total


def _fmt(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:,.0f} {unit}" if unit == "B" else f"{n:,.1f} {unit}"
        n /= 1024
    return str(n)


def _unsafe(path: Path) -> str | None:
    """Reason the folder must never be emptied, else None."""
    p = path.resolve()
    base = BASE_DIR.resolve()
    if p == Path(p.anchor) or p == Path.home().resolve():
        return "it is a drive root or the home folder"
    if p == base or p in base.parents:
        return "it contains the program itself"
    for name in PROTECTED:
        prot = (base / name).resolve()
        if p == prot or prot in p.parents or p in prot.parents:
            return f"it is, or contains, the program folder '{name}'"
    return None


def plan(s: Settings, db: bool, images: bool, incoming: bool, logs: bool) -> list[tuple[str, Path, str]]:
    """(label, path, kind) where kind 'dir' = delete contents, 'file' = delete this file."""
    items: list[tuple[str, Path, str]] = []
    if db:
        for suffix in ("", "-wal", "-shm"):
            f = Path(str(s.database_path) + suffix)
            if f.exists():
                items.append(("database" if not suffix else f"database {suffix[1:]}", f, "file"))
    if images:
        for label, d in (("archive (images)", s.archive_dir), ("processing", s.processing_dir),
                         ("error", s.error_dir), ("uploaded", s.uploaded_dir)):
            items.append((label, d, "dir"))
    if incoming:
        items.append(("incoming (waiting files)", s.incoming_dir, "dir"))
    if logs:
        items.append(("logs", s.log_dir, "logdir"))
    return [(a, b, c) for a, b, c in items if b.exists()]


def _empty_dir(d: Path, keep_names: set[str] = frozenset(), keep_dirs: bool = False) -> int:
    n = 0
    for child in list(d.iterdir()):
        if child.name in keep_names:
            continue
        try:
            if child.is_dir() and not child.is_symlink():
                if keep_dirs:                       # sensor folders: empty them, keep them
                    n += _empty_dir(child, keep_names, keep_dirs)
                    continue
                shutil.rmtree(child)
            else:
                child.unlink()
            n += 1
        except OSError as exc:
            print(f"  could not delete {child}: {exc}")
    return n


def run(args: list[str], s: Settings, running_pid) -> int:
    if any(a in {"-h", "--help"} for a in args):
        print(__doc__)
        return 0
    known = {"--db-only", "--images-only", "--incoming", "--logs", "--dry-run", "--yes"}
    bad = [a for a in args if a not in known]
    if bad:
        print(f"unknown option: {' '.join(bad)}\n{__doc__}")
        return 2
    if "--db-only" in args and "--images-only" in args:
        print("--db-only and --images-only cannot be combined (leave both out to clear both).")
        return 2

    db = "--images-only" not in args
    images = "--db-only" not in args
    items = plan(s, db, images, "--incoming" in args, "--logs" in args)

    for label, path, _ in items:
        why = _unsafe(path)
        if why:
            print(f"Refusing: {label} folder {path} - {why}. Fix the path in .env.")
            return 2

    pid = running_pid()
    if pid and "--dry-run" not in args:
        print(f"The agent is running (pid {pid}). Stop it first: run stop")
        return 1

    print("Data to delete:" if "--dry-run" not in args else "Would delete (dry run):")
    if not items:
        print("  nothing - already empty.")
        return 0
    for label, path, kind in items:
        files, size = _size(path)
        print(f"  {label:26} {files:>9,} files  {_fmt(size):>10}   {path}")
    print("Kept: .env, Google sign-in (credentials/), backups, files already on Google Drive/Sheets.")
    if "--dry-run" in args:
        return 0

    if "--yes" not in args:
        if not sys.stdin.isatty():
            print("Not a terminal - add --yes to confirm.")
            return 1
        print("\nThis cannot be undone.")
        if input("Type CLEAR to delete: ").strip() != "CLEAR":
            print("Cancelled - nothing deleted.")
            return 1

    for label, path, kind in items:
        try:
            if kind == "file":
                path.unlink()
            elif kind == "logdir":
                _empty_dir(path, keep_names=KEEP_LOG_FILES)
            else:
                _empty_dir(path, keep_dirs=(path == s.incoming_dir))
        except OSError as exc:
            print(f"  could not clear {label}: {exc}")
            return 1
    print("\nDone. Start again with: run")
    return 0
