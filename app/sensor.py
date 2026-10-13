"""`run sensor` - parameters, metrics and accuracy of the sensor in one command.

    run sensor                          report: sensor changes, per-tool metrics, accuracy (if labelled)
    run sensor --days N                 metrics window (default 7)
    run sensor --set "Tool1 threshold 70->65"   write down a change made on the sensor (shown with NG% before/after)
    run sensor --sample N               write reports\\label_sample_<date>.csv: N inspections to check by eye
    run sensor --labels file.csv        import the checked answers (columns uid,truth with OK/NG), then report

The sensor only says OK/NG by its own rule; it cannot say whether that was right. Accuracy therefore needs a person
to check a sample (--sample) and enter the true answer (--labels). The sample takes equal numbers of sensor-OK and
sensor-NG pieces so the rare NG side is measured too; the totals are then re-weighted to the real OK/NG counts.
Saves reports\\sensor-<date>.txt.
"""
from __future__ import annotations

import csv
import json
import math
import random
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import text

from app.config import BASE_DIR

HEADER = ["uid", "timestamp", "sensor", "sensor_result", "score", "failing_tools", "image_file", "truth"]


def _hour(d: datetime) -> str:
    return d.strftime("%Y-%m-%d %H")


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95 % interval for a proportion k/n (honest about small samples)."""
    if n == 0:
        return 0.0, 1.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def _pct(k: int, n: int) -> str:
    if not n:
        return "-"
    lo, hi = wilson(k, n)
    return f"{100 * k / n:.1f}% ({100 * lo:.1f}-{100 * hi:.1f}%)"


# ------------------------------------------------------------------
# notes (parameters)
# ------------------------------------------------------------------

def add_note(repo, note: str, now: datetime) -> None:
    with repo.engine.begin() as c:
        c.execute(text("INSERT INTO sensor_note (at, text) VALUES (:a, :t)"),
                  {"a": now.strftime("%Y-%m-%d %H:%M:%S"), "t": note[:500]})


def _ng_rate(repo, a: datetime, b: datetime) -> tuple[int, float | None]:
    with repo.engine.connect() as c:
        r = c.execute(text("SELECT SUM(total), SUM(fail_count) FROM hourly_stats WHERE hour >= :a AND hour < :b"),
                      {"a": _hour(a), "b": _hour(b)}).one()
    total, ng = r[0] or 0, r[1] or 0
    return total, (100 * ng / total if total else None)


def notes_section(repo, now: datetime, limit: int = 8) -> list[str]:
    with repo.engine.connect() as c:
        rows = c.execute(text("SELECT at, text FROM sensor_note ORDER BY at DESC LIMIT :n"), {"n": limit}).all()
    if not rows:
        return ["  (none written down. Record a change with: run sensor --set \"Tool1 threshold 70->65\")"]
    out = []
    for at, txt in rows:
        t = datetime.strptime(at, "%Y-%m-%d %H:%M:%S")
        n1, before = _ng_rate(repo, t - timedelta(hours=24), t)
        n2, after = _ng_rate(repo, t, min(t + timedelta(hours=24), now))
        eff = ""
        if before is not None and after is not None and n1 >= 500 and n2 >= 500:
            eff = f"   NG 24h before {before:.2f}% -> after {after:.2f}%"
        out.append(f"  {at[:16]}  {txt}{eff}")
    return out


# ------------------------------------------------------------------
# metrics
# ------------------------------------------------------------------

def tool_metrics(repo, now: datetime, days: int) -> list[dict]:
    a = _hour(now - timedelta(days=days))
    sql = """SELECT sensor_id, program_no, tool_no, MAX(tool_name), SUM(count), SUM(ng_count), SUM(value_sum),
                    SUM(value_count), MIN(value_min), MIN(ok_value_min)
             FROM hourly_tool_stats WHERE hour >= :a GROUP BY sensor_id, program_no, tool_no
             ORDER BY sensor_id, program_no, tool_no"""
    with repo.engine.connect() as c:
        rows = c.execute(text(sql), {"a": a}).all()
    return [{"sensor": r[0], "program": r[1], "tool": r[2], "name": r[3], "n": r[4] or 0, "ng": r[5] or 0,
             "avg": r[6] / r[7] if r[7] else None, "min": r[8], "ok_min": r[9]} for r in rows]


def metrics_section(repo, now: datetime, days: int) -> list[str]:
    rows = tool_metrics(repo, now, days)
    if not rows:
        return [f"  no inspections in the last {days} days"]
    out = [f"  {'Sensor/Tool':32}{'Inspections':>12}{'NG %':>8}{'Avg value':>11}{'Lowest':>9}{'Lowest OK':>11}"]
    f = lambda v: "-" if v is None else f"{v:.1f}"   # noqa: E731
    for r in rows:
        who = f"{r['sensor'] or '-'} T{r['tool']} {r['name'] or ''}".strip()[:31]
        ngp = f"{100 * r['ng'] / r['n']:.2f}" if r["n"] else "-"
        out.append(f"  {who:32}{r['n']:>12,}{ngp:>8}{f(r['avg']):>11}{f(r['min']):>9}{f(r['ok_min']):>11}")
    out.append("  'Lowest OK' = the smallest value the sensor still passed, so the pass line is at or just below it.")
    return out


# ------------------------------------------------------------------
# labels and accuracy
# ------------------------------------------------------------------

def make_sample(repo, n: int, days: int, now: datetime, seed: int | None = None, base: Path | None = None) -> tuple[Path | None, int]:
    """Half sensor-OK, half sensor-NG, random, not yet labelled. Returns (file, rows)."""
    a = (now - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    rng = random.Random(seed)
    picked: list = []
    with repo.engine.connect() as c:
        for status in ("PASS", "FAIL"):
            ids = [r[0] for r in c.execute(text(
                "SELECT i.id FROM inspection i LEFT JOIN sensor_label l ON l.uid = i.uid"
                " WHERE i.analysis_status = :s AND i.timestamp >= :a AND l.uid IS NULL AND i.image_file IS NOT NULL"),
                {"s": status, "a": a})]
            picked += rng.sample(ids, min(len(ids), n // 2 + (n % 2 if status == "PASS" else 0)))
        rows = []
        for i in range(0, len(picked), 500):
            chunk = picked[i:i + 500]
            q = text("SELECT uid, timestamp, camera_id, analysis_status, score, tools_json, image_file FROM inspection"
                     f" WHERE id IN ({','.join(str(int(x)) for x in chunk)})")
            rows += list(c.execute(q))
    if not rows:
        return None, 0
    rows.sort(key=lambda r: (r[1] or "", r[0]))
    out = (base or BASE_DIR) / "reports"
    out.mkdir(exist_ok=True)
    path = out / f"label_sample_{now:%Y-%m-%d_%H%M}.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(HEADER)
        for uid, ts, cam, st, score, tj, img in rows:
            try:
                bad = ",".join(f"T{t['no']}" for t in json.loads(tj or "[]") if str(t.get("status")).upper() == "NG")
            except (ValueError, TypeError, KeyError):
                bad = ""
            w.writerow([uid, ts, cam, "OK" if st == "PASS" else "NG", score, bad, img, ""])
    return path, len(rows)


def import_labels(repo, path: Path, now: datetime) -> tuple[int, int, list[str]]:
    """(saved, skipped_blank, problems). truth must be OK or NG; unknown uids are reported."""
    saved = blank = 0
    problems: list[str] = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames or "uid" not in reader.fieldnames or "truth" not in reader.fieldnames:
            return 0, 0, ["the file needs the columns 'uid' and 'truth' (use the file from --sample)"]
        rows = list(reader)
    with repo.engine.begin() as c:
        known = {r[0] for r in c.execute(text("SELECT uid FROM inspection"))}
        for i, r in enumerate(rows, start=2):
            truth = (r.get("truth") or "").strip().upper()
            uid = (r.get("uid") or "").strip()
            if not truth:
                blank += 1
                continue
            if truth not in {"OK", "NG"}:
                problems.append(f"line {i}: truth '{r['truth']}' must be OK or NG")
                continue
            if uid not in known:
                problems.append(f"line {i}: uid {uid} is not in the database")
                continue
            c.execute(text("INSERT INTO sensor_label (uid, truth, labeled_at) VALUES (:u, :t, :n)"
                           " ON CONFLICT(uid) DO UPDATE SET truth = excluded.truth, labeled_at = excluded.labeled_at"),
                      {"u": uid, "t": truth, "n": now})
            saved += 1
    return saved, blank, problems


def accuracy(repo) -> dict | None:
    """Confusion counts of labelled pieces, re-weighted to the real OK/NG totals of the same period."""
    sql = text("SELECT i.analysis_status, l.truth, i.timestamp, i.tools_json FROM sensor_label l"
               " JOIN inspection i ON i.uid = l.uid WHERE i.analysis_status IN ('PASS', 'FAIL')")
    with repo.engine.connect() as c:
        rows = c.execute(sql).all()
    if not rows:
        return None
    cm = Counter()
    fr_tools: Counter = Counter()
    ts = []
    for st, truth, t, tj in rows:
        s = "OK" if st == "PASS" else "NG"
        cm[(s, truth)] += 1
        if t:
            ts.append(t)
        if s == "NG" and truth == "OK":
            try:
                for x in json.loads(tj or "[]"):
                    if str(x.get("status")).upper() == "NG":
                        fr_tools[f"T{x['no']}"] += 1
            except (ValueError, TypeError, KeyError):
                pass
    n_ok = cm[("OK", "OK")] + cm[("OK", "NG")]
    n_ng = cm[("NG", "OK")] + cm[("NG", "NG")]
    res = {"cm": cm, "n": len(rows), "n_sensor_ok": n_ok, "n_sensor_ng": n_ng, "fr_tools": fr_tools,
           "from": min(ts) if ts else None, "to": max(ts) if ts else None, "weighted": None}
    if ts and n_ok and n_ng:
        a, b = min(ts)[:13], max(ts)[:13]
        with repo.engine.connect() as c:
            p_ok, p_ng = c.execute(text("SELECT SUM(pass_count), SUM(fail_count) FROM hourly_stats"
                                        " WHERE hour >= :a AND hour <= :b"), {"a": a, "b": b}).one()
        p_ok, p_ng = p_ok or 0, p_ng or 0
        if p_ok + p_ng:
            wrong = p_ok * cm[("OK", "NG")] / n_ok + p_ng * cm[("NG", "OK")] / n_ng
            res["weighted"] = 1 - wrong / (p_ok + p_ng)
            res["pop"] = (p_ok, p_ng)
    return res


def accuracy_section(repo) -> list[str]:
    acc = accuracy(repo)
    if not acc:
        return ["  no checked pieces yet. 1) run sensor --sample 200   2) fill the 'truth' column (OK/NG) by looking at "
                "the image   3) run sensor --labels <file>"]
    cm = acc["cm"]
    out = [f"  {acc['n']:,} checked pieces ({acc['from'][:10]} to {acc['to'][:10]})",
           "                      truth OK   truth NG",
           f"  sensor said OK   {cm[('OK', 'OK')]:>10,} {cm[('OK', 'NG')]:>10,}",
           f"  sensor said NG   {cm[('NG', 'OK')]:>10,} {cm[('NG', 'NG')]:>10,}",
           f"  False reject (sensor NG, truth OK): {_pct(cm[('NG', 'OK')], acc['n_sensor_ng'])}   of the pieces it rejected",
           f"  Missed defect (sensor OK, truth NG): {_pct(cm[('OK', 'NG')], acc['n_sensor_ok'])}   of the pieces it passed"]
    if acc["weighted"] is not None:
        out.append(f"  Estimated accuracy on all {sum(acc['pop']):,} pieces of that period: {100 * acc['weighted']:.2f}%")
    if acc["fr_tools"]:
        out.append("  False rejects by failing tool: " + ", ".join(f"{k} x{v}" for k, v in acc["fr_tools"].most_common(5)))
    if acc["n_sensor_ng"] < 30 or acc["n_sensor_ok"] < 30:
        out.append("  ! fewer than 30 checked pieces on one side - the ranges above are wide; check more.")
    out.append("  (the % in brackets is the 95 % range; it narrows as more pieces are checked)")
    return out


# ------------------------------------------------------------------

def run(args: list[str], s, _running_pid=None, now: datetime | None = None, base: Path | None = None) -> int:
    from app.database.repository import DatabaseRepository

    opt: dict = {"days": 7, "set": None, "sample": None, "labels": None}
    it = iter(args)
    try:
        for a in it:
            if a == "--days":
                opt["days"] = max(int(next(it)), 1)
            elif a == "--set":
                opt["set"] = next(it).strip()
            elif a == "--sample":
                opt["sample"] = max(int(next(it)), 2)
            elif a == "--labels":
                opt["labels"] = Path(next(it))
            else:
                print(f"Unknown option {a}. See: run help")
                return 2
    except (StopIteration, ValueError):
        print("Bad option value. Examples: run sensor --days 7 | --sample 200 | --labels file.csv | --set \"text\"")
        return 2

    now = now or datetime.now()
    repo = DatabaseRepository(s.database_path)
    try:
        if opt["set"]:
            add_note(repo, opt["set"], now)
            print(f"Written down: {now:%Y-%m-%d %H:%M}  {opt['set']}")
        if opt["sample"]:
            path, n = make_sample(repo, opt["sample"], max(opt["days"], 1), now, base=base)
            if not path:
                print("No inspections with images to sample in that period (try --days 30).")
                return 1
            print(f"Wrote {n} pieces to {path}\nOpen it, look at each image_file and type OK or NG in the 'truth' column "
                  f"(leave blank to skip), then: run sensor --labels \"{path}\"")
            return 0
        if opt["labels"]:
            if not opt["labels"].exists():
                print(f"File not found: {opt['labels']}")
                return 2
            saved, blank, problems = import_labels(repo, opt["labels"], now)
            print(f"Imported {saved} answers ({blank} blank skipped).")
            for p in problems[:10]:
                print("  ! " + p)
            if problems and not saved:
                return 2
        lines = [f"Sensor report {now:%Y-%m-%d %H:%M}", "",
                 "1. Changes written down on the sensor (parameters)"] + notes_section(repo, now) + [
                 "", f"2. Metrics per tool, last {opt['days']} days"] + metrics_section(repo, now, opt["days"]) + [
                 "", "3. Accuracy (against pieces checked by a person)"] + accuracy_section(repo)
        print("\n".join(lines))
        out = (base or BASE_DIR) / "reports"
        out.mkdir(exist_ok=True)
        (out / f"sensor-{now:%Y-%m-%d}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return 0
    finally:
        repo.dispose()
