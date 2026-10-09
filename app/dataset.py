"""
`run export-dataset <out-folder> [options]` - copy archived images into a versioned, labelled training set.

    run export-dataset D:\\datasets\\lot-A                       all NG + the same number of random OK images
    run export-dataset D:\\datasets\\lot-A --from 2026-10-06 --to 2026-10-08
    run export-dataset D:\\datasets\\lot-A --ok-per-ng 3          3 OK images for every NG
    run export-dataset D:\\datasets\\lot-A --sensor IV4-01 --limit 5000
    run export-dataset D:\\datasets\\lot-A --dry-run              count only, copy nothing

Output:  <out>/images/<NG|OK>/<file>   <out>/labels.csv   <out>/dataset.json
The label is the sensor's own verdict (FAIL = NG, PASS = OK); UNKNOWN is never exported. dataset.json holds the
dataset version (ds-<date>-<hash of the file list>), the options, counts and the program versions/settings that
produced the results (lineage), so a model trained on it can always be traced back to its data.
Images are copied, never moved. Images already deleted by retention or shrunk by IV4_RESIZE_OK are reported.
"""
from __future__ import annotations

import csv
import hashlib
import json
import random
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import text

LABEL = {"FAIL": "NG", "PASS": "OK"}


def _parse(args: list[str]) -> dict | str:
    o = {"out": None, "from": None, "to": None, "sensor": None, "ok_per_ng": 1.0, "limit": None, "dry": False}
    it = iter(args)
    try:
        for a in it:
            if a == "--dry-run":
                o["dry"] = True
            elif a in ("--from", "--to", "--sensor"):
                o[a[2:]] = next(it)
            elif a == "--ok-per-ng":
                o["ok_per_ng"] = float(next(it))
            elif a == "--limit":
                o["limit"] = int(next(it))
            elif a.startswith("--"):
                return f"Unknown option {a}"
            elif o["out"] is None:
                o["out"] = a
            else:
                return f"Unexpected argument {a}"
    except (StopIteration, ValueError):
        return "Missing or invalid value for an option"
    for k in ("from", "to"):
        if o[k]:
            try:
                datetime.strptime(o[k], "%Y-%m-%d")
            except ValueError:
                return f"--{k} must look like 2026-10-08"
    return o


def _select(repo, o: dict) -> tuple[list[dict], list[dict]]:
    where, params = ["analysis_status IN ('FAIL', 'PASS')", "folder IS NOT NULL", "image_file IS NOT NULL"], {}
    if o["from"]:
        where.append("timestamp >= :a")
        params["a"] = o["from"]
    if o["to"]:
        where.append("timestamp < :b")
        params["b"] = (datetime.strptime(o["to"], "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")   # --to day is included
    if o["sensor"]:
        where.append("camera_id = :s")
        params["s"] = o["sensor"]
    sql = ("SELECT uid, timestamp, camera_id, program_no, analysis_status, analysis_reason, score, folder, image_file,"
           f" app_version, config_hash FROM inspection WHERE {' AND '.join(where)} ORDER BY timestamp, uid")
    cols = ["uid", "timestamp", "sensor", "program", "status", "reason", "score", "folder", "image", "app_version", "config_hash"]
    ng, ok = [], []
    with repo.engine.connect() as c:
        for r in c.execute(text(sql), params):
            d = dict(zip(cols, r))
            (ng if d["status"] == "FAIL" else ok).append(d)
    return ng, ok


def run(args: list[str], s, _running_pid=None) -> int:
    from app.database.repository import DatabaseRepository
    from app.version import read_version
    if "-h" in args or "--help" in args:
        print(__doc__)
        return 0
    o = _parse(args)
    if isinstance(o, str) or not o["out"]:
        print(o if isinstance(o, str) else "Give an output folder.\n")
        print(__doc__)
        return 2
    repo = DatabaseRepository(s.database_path)
    try:
        ng, ok = _select(repo, o)
    finally:
        repo.dispose()
    rng = random.Random(42)                        # same options + same data = same set
    want_ok = int(round(len(ng) * o["ok_per_ng"]))
    ok = sorted(rng.sample(ok, want_ok) if want_ok < len(ok) else ok, key=lambda d: (d["timestamp"] or "", d["uid"]))
    chosen = ng + ok
    if o["limit"]:
        chosen = sorted(chosen, key=lambda d: (d["timestamp"] or "", d["uid"]))[: o["limit"]]
    present, missing = [], 0
    for d in chosen:
        src = s.archive_dir / d["folder"] / d["image"]
        if src.exists():
            present.append((d, src))
        else:
            missing += 1
    n_ng = sum(1 for d, _ in present if d["status"] == "FAIL")
    print(f"Found NG {len(ng):,}, OK {len(ok):,} selected | images present {len(present):,} "
          f"(NG {n_ng:,}, OK {len(present) - n_ng:,}) | image already deleted: {missing:,}")
    if o["dry"] or not present:
        print("(dry run - nothing copied)" if o["dry"] else "Nothing to export.")
        return 0
    out = Path(o["out"])
    if (out / "dataset.json").exists():
        print(f"{out} already holds a dataset. Use a new folder so versions are never mixed.")
        return 1
    rows, h = [], hashlib.sha256()
    for d, src in present:
        label = LABEL[d["status"]]
        name = f"{(d['timestamp'] or 'na').replace(' ', 'T').replace(':', '')}_{d['sensor'] or 'na'}_{d['uid']}{src.suffix}"
        dst = out / "images" / label / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        h.update(f"{label}/{name}\n".encode())
        rows.append([f"images/{label}/{name}", label, d["uid"], d["timestamp"], d["sensor"], d["program"], d["score"],
                     d["reason"], d["app_version"], d["config_hash"]])
    with open(out / "labels.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["file", "label", "uid", "timestamp", "sensor", "program", "score", "reason", "app_version", "config_hash"])
        w.writerows(rows)
    version = f"ds-{datetime.now():%Y%m%d}-{h.hexdigest()[:8]}"
    (out / "dataset.json").write_text(json.dumps({
        "dataset_version": version, "created_at": datetime.now().isoformat(timespec="seconds"),
        "exported_by": f"IV4 Data Agent {read_version()}",
        "options": {k: o[k] for k in ("from", "to", "sensor", "ok_per_ng", "limit")},
        "counts": {"NG": n_ng, "OK": len(present) - n_ng, "skipped_missing_image": missing},
        "produced_by": sorted({(r[8], r[9]) for r in rows if r[8]}),
        "note": "label = the sensor's own verdict (FAIL=NG, PASS=OK). produced_by = (program version, settings hash); see `run lineage`.",
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Dataset {version}: {len(rows):,} images -> {out}")
    return 0
