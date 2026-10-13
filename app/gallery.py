"""Images you can browse: every archived image also appears directly in its hour folder.

    archive\\2026-10-13\\NG\\09\\091503_T1_00292_13102026_091503.jpg     <- open the folder, see all NG of 09:00
    archive\\2026-10-13\\NG\\09\\<uid>\\...                               (the piece's own folder, unchanged)

The extra entry is a hard link (same file on disk, no extra space; Explorer may still count it twice).
If the disk cannot hard-link (FAT/exFAT), NG and UNKNOWN images are copied and OK images are skipped.
Retention and disk pruning delete the hour folder, so these go with it. Turn off with IV4_FLAT_IMAGES=false.

`run images [--day YYYY-MM-DD | --all]` adds them for pieces archived before this existed (default: today).
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import text

log = logging.getLogger("iv4.gallery")
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def flat_name(timestamp: str | None, tools: list | None, image: str, ng: bool) -> str:
    """'091503_T1_<image>' for NG (failing tools), '091503_<image>' for OK: sorts by time inside the hour."""
    hms = ""
    if timestamp and len(timestamp) >= 19:
        hms = timestamp[11:19].replace(":", "")
    bad = ""
    if ng and tools:
        bad = "".join(f"T{t.get('no')}" for t in tools if str(t.get("status") or "").upper() == "NG")
    parts = [p for p in (hms, bad, _SAFE.sub("_", image)) if p]
    return "_".join(parts)


def link(uid_dir: Path, image: str | None, timestamp: str | None, tools: list | None, ng: bool) -> str | None:
    """Put the image next to the uid folder. Returns 'linked' / 'copied' / None (nothing done)."""
    if not image:
        return None
    src = uid_dir / image
    if not src.is_file():
        return None
    dst = uid_dir.parent / flat_name(timestamp, tools, image, ng)
    if dst.exists():
        return None
    try:
        os.link(src, dst)
        return "linked"
    except OSError:
        if not ng:
            return None                     # OK images are many: never double the disk use
        try:
            shutil.copy2(src, dst)
            return "copied"
        except OSError as exc:
            log.warning("[GALLERY] cannot copy %s: %s", src, exc)
            return None


def backfill(repo, archive_dir: Path, day_from: str, day_to: str) -> dict:
    """Add flat images for pieces archived between two days (inclusive)."""
    n = {"linked": 0, "copied": 0, "skipped": 0}
    sql = text("SELECT folder, image_file, timestamp, tools_json, analysis_status FROM inspection"
               " WHERE folder >= :a AND folder < :b AND image_file IS NOT NULL")
    end = (date.fromisoformat(day_to) + timedelta(days=1)).isoformat()
    with repo.engine.connect() as c:
        for folder, image, ts, tj, status in c.execute(sql, {"a": day_from, "b": end}):
            try:
                tools = json.loads(tj) if tj else None
            except ValueError:
                tools = None
            r = link(archive_dir / folder, image, ts, tools, status != "PASS")
            n[r or "skipped"] += 1
    return n


def run(args: list[str], s, _running_pid=None) -> int:
    from app.database.repository import DatabaseRepository

    today = date.today().isoformat()
    a = b = today
    if "--all" in args:
        a, b = "0000-00-00", "9999-12-30"
    elif "--day" in args:
        try:
            a = b = date.fromisoformat(args[args.index("--day") + 1]).isoformat()
        except (IndexError, ValueError):
            print("Usage: run images [--day YYYY-MM-DD | --all]")
            return 2
    repo = DatabaseRepository(s.database_path)
    try:
        n = backfill(repo, s.archive_dir, a, b)
    finally:
        repo.dispose()
    print(f"Images placed in their hour folders: {n['linked']:,} linked, {n['copied']:,} copied, "
          f"{n['skipped']:,} already there / not found.")
    print(f"Open {s.archive_dir}\\<date>\\NG\\<hour> (or OK) to browse them.")
    return 0
