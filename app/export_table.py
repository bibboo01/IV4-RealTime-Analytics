"""`run export-table` - one file per day with every inspection as a flat table, for analysis and model training.

    run export-table                         yesterday (what a nightly task wants), saved to exports\\
    run export-table --day 2026-10-10
    run export-table --from 2026-10-08 --to 2026-10-10      one file per day
    run export-table --format csv            gzip CSV (no extra library); default is Parquet
    run export-table --out D:\\share\\iv4     another folder (a NAS / shared drive works well)
    run export-table --force                 overwrite a day that was already exported

Columns: day, uid, timestamp, sensor, program, trigger_no, status (PASS/FAIL), reason, score, time_ms,
image_file, folder, app_version, config_hash, then tool<N>_status / tool<N>_value for each tool.
Nothing is deleted or changed; a day is written to a temporary file and renamed, so a half-written file never
looks finished. Parquet needs the 'pyarrow' library (requirements-export.txt), installed once on first use.

Schedule nightly (Windows): schtasks /create /tn IV4Export /tr "D:\\iv4\\run.bat export-table" /sc daily /st 02:30
"""
from __future__ import annotations

import csv
import gzip
import json
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import text

from app.config import BASE_DIR

BASE_COLS = ["day", "uid", "timestamp", "sensor", "program", "trigger_no", "status", "reason", "score", "time_ms",
             "image_file", "folder", "app_version", "config_hash"]
CHUNK_HOURS = 1


def _parse(args: list[str]) -> dict | str:
    o = {"from": None, "to": None, "out": None, "fmt": "parquet", "force": False}
    it = iter(args)
    try:
        for a in it:
            if a == "--day":
                o["from"] = o["to"] = next(it)
            elif a in ("--from", "--to", "--out"):
                o[a[2:]] = next(it)
            elif a == "--format":
                o["fmt"] = next(it).lower()
            elif a == "--force":
                o["force"] = True
            else:
                return f"Unknown option {a}"
    except StopIteration:
        return "Missing value for an option"
    if o["fmt"] not in ("parquet", "csv"):
        return "--format must be parquet or csv"
    try:
        for k in ("from", "to"):
            if o[k]:
                o[k] = datetime.strptime(o[k], "%Y-%m-%d").date()
    except ValueError:
        return "dates must look like 2026-10-08"
    if not o["from"]:
        o["from"] = o["to"] = date.today() - timedelta(days=1)
    elif not o["to"]:
        o["to"] = o["from"]
    if o["to"] < o["from"]:
        return "--to is before --from"
    return o


def ensure_pyarrow() -> str | None:
    try:
        import pyarrow  # noqa: F401
        return None
    except ImportError:
        pass
    req = Path(__file__).resolve().parent.parent / "requirements-export.txt"
    print("Parquet export needs the 'pyarrow' library; installing it once (needs internet)...")
    try:
        r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", str(req)] if req.exists()
                           else [sys.executable, "-m", "pip", "install", "-q", "pyarrow"],
                           capture_output=True, text=True, timeout=900)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"could not run pip: {exc}"
    if r.returncode != 0:
        msg = (r.stderr or r.stdout).strip().splitlines()
        return msg[-1][:200] if msg else "pip failed"
    try:
        import pyarrow  # noqa: F401,F811
        return None
    except ImportError as exc:
        return str(exc)


def _tool_numbers(repo, day: str) -> list[int]:
    with repo.engine.connect() as c:
        return [r[0] for r in c.execute(text(
            "SELECT DISTINCT tool_no FROM hourly_tool_stats WHERE hour >= :a AND hour < :b ORDER BY tool_no"),
            {"a": day, "b": (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")})]


def _rows(repo, day: str, tools: list[int]):
    """Yield one-hour lists of rows (dicts) so memory stays small."""
    start = datetime.strptime(day, "%Y-%m-%d")
    sql = text("SELECT uid, timestamp, camera_id, program_no, trigger_no, analysis_status, analysis_reason, score,"
               " inspection_time_ms, image_file, folder, app_version, config_hash, tools_json FROM inspection"
               " WHERE timestamp >= :a AND timestamp < :b ORDER BY timestamp, uid")
    for h in range(0, 24, CHUNK_HOURS):
        a = (start + timedelta(hours=h)).strftime("%Y-%m-%d %H:%M:%S")
        b = (start + timedelta(hours=h + CHUNK_HOURS)).strftime("%Y-%m-%d %H:%M:%S")
        out = []
        with repo.engine.connect() as c:
            for r in c.execute(sql, {"a": a, "b": b}):
                row = dict(zip(BASE_COLS, (day,) + tuple(r[:13])))
                try:
                    found = {t["no"]: t for t in json.loads(r[13] or "[]")}
                except (ValueError, TypeError, KeyError):
                    found = {}
                for n in tools:
                    t = found.get(n) or {}
                    row[f"tool{n}_status"] = t.get("status")
                    row[f"tool{n}_value"] = t.get("value")
                out.append(row)
        if out:
            yield out


def _write_csv(chunks, cols, path: Path) -> int:
    tmp = path.with_name(path.name + ".tmp")
    n = 0
    with gzip.open(tmp, "wt", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for rows in chunks:
            w.writerows(rows)
            n += len(rows)
    tmp.replace(path)
    return n


def _write_parquet(chunks, cols, tools, path: Path) -> int:
    import pyarrow as pa
    import pyarrow.parquet as pq
    kinds = {"program": pa.int64(), "trigger_no": pa.int64(), "score": pa.float64(), "time_ms": pa.int64()}
    fields = [pa.field(c, kinds.get(c) or (pa.float64() if c.endswith("_value") else pa.string())) for c in cols]
    schema = pa.schema(fields)
    tmp = path.with_name(path.name + ".tmp")
    n = 0
    with pq.ParquetWriter(tmp, schema, compression="snappy") as w:
        for rows in chunks:
            def conv(c, v):
                if v is None or c in ("program", "trigger_no", "score", "time_ms") or c.endswith("_value"):
                    try:
                        return None if v is None else (float(v) if c == "score" or c.endswith("_value") else int(v))
                    except (TypeError, ValueError):
                        return None
                return str(v)
            w.write_table(pa.table({c: [conv(c, r.get(c)) for r in rows] for c in cols}, schema=schema))
            n += len(rows)
    tmp.replace(path)
    return n


def run(args: list[str], s, _running_pid=None, base: Path | None = None) -> int:
    o = _parse(args)
    if isinstance(o, str):
        print(o if not args or args[0] not in ("-h", "--help") else __doc__)
        return 2 if not (args and args[0] in ("-h", "--help")) else 0
    fmt = o["fmt"]
    if fmt == "parquet":
        err = ensure_pyarrow()
        if err:
            print(f"Parquet is not available ({err}); writing gzip CSV instead (--format csv).")
            fmt = "csv"
    out_dir = Path(o["out"]) if o["out"] else (base or BASE_DIR) / "exports"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"Cannot create {out_dir}: {exc}")
        return 1
    from app.database.repository import DatabaseRepository
    repo = DatabaseRepository(s.database_path)
    wrote = skipped = empty = 0
    try:
        d = o["from"]
        while d <= o["to"]:
            day = d.isoformat()
            path = out_dir / f"inspections_{day}.{'parquet' if fmt == 'parquet' else 'csv.gz'}"
            d += timedelta(days=1)
            if path.exists() and not o["force"]:
                print(f"{day}: already exported ({path.name}); --force to redo")
                skipped += 1
                continue
            tools = _tool_numbers(repo, day)
            cols = BASE_COLS + [f"tool{n}_{k}" for n in tools for k in ("status", "value")]
            chunks = _rows(repo, day, tools)
            n = _write_parquet(chunks, cols, tools, path) if fmt == "parquet" else _write_csv(chunks, cols, path)
            if n == 0:
                path.unlink(missing_ok=True)
                print(f"{day}: no inspections")
                empty += 1
            else:
                print(f"{day}: {n:,} rows -> {path}")
                wrote += 1
    finally:
        repo.dispose()
    return 0 if wrote or skipped else 1
