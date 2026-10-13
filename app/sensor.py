"""`run sensor` - parameters, metrics and accuracy of the sensor in one command.

    run sensor                          report: sensor changes, per-tool metrics, accuracy (if labelled)
    run sensor --days N                 metrics window (default 7)
    run sensor --day 2026-10-13         only that day (metrics, check without labels, --sample)
    run sensor --set "Tool1 threshold 70->65"   write down a change made on the sensor (shown with NG% before/after)
    run sensor --sample N               write reports\\label_sample_<date>.csv: N inspections to check by eye
    run sensor --labels file.csv        import the checked answers (columns uid,truth with OK/NG), then report

The sensor only says OK/NG by its own rule; it cannot say whether that was right. Accuracy therefore needs a person
to check a sample (--sample) and enter the true answer (--labels). The sample takes equal numbers of sensor-OK and
sensor-NG pieces so the rare NG side is measured too; the totals are then re-weighted to the real OK/NG counts.
Saves reports\\sensor-<date>.txt and, once pieces are checked, adds a row to reports\\accuracy.csv.
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

HEADER = ["uid", "timestamp", "sensor", "program", "trigger_no", "time_ms", "sensor_result", "score", "failing_tools"]
TAIL = ["image_file", "folder", "system_truth", "truth"]


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

def _window(now: datetime, days: int, day: datetime | None) -> tuple[datetime, datetime]:
    if day:
        start = day.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, start + timedelta(days=1)
    return now - timedelta(days=days), now + timedelta(seconds=1)


def tool_metrics(repo, now: datetime, days: int, day: datetime | None = None) -> list[dict]:
    a, b = _window(now, days, day)
    sql = """SELECT sensor_id, program_no, tool_no, MAX(tool_name), SUM(count), SUM(ng_count), SUM(value_sum),
                    SUM(value_count), MIN(value_min), MIN(ok_value_min)
             FROM hourly_tool_stats WHERE hour >= :a AND hour < :b GROUP BY sensor_id, program_no, tool_no
             ORDER BY sensor_id, program_no, tool_no"""
    with repo.engine.connect() as c:
        rows = c.execute(text(sql), {"a": _hour(a), "b": _hour(b) if day else "9999"}).all()
    return [{"sensor": r[0], "program": r[1], "tool": r[2], "name": r[3], "n": r[4] or 0, "ng": r[5] or 0,
             "avg": r[6] / r[7] if r[7] else None, "min": r[8], "ok_min": r[9]} for r in rows]


def metrics_section(repo, now: datetime, days: int, day: datetime | None = None) -> list[str]:
    rows = tool_metrics(repo, now, days, day)
    if not rows:
        return [f"  no inspections on {day:%Y-%m-%d}" if day else f"  no inspections in the last {days} days"]
    out = [f"  {'Sensor/Tool':32}{'Inspections':>12}{'NG %':>8}{'Avg value':>11}{'Lowest':>9}{'Lowest OK':>11}"]
    f = lambda v: "-" if v is None else f"{v:.1f}"   # noqa: E731
    for r in rows:
        who = f"{r['sensor'] or '-'} T{r['tool']} {r['name'] or ''}".strip()[:31]
        ngp = f"{100 * r['ng'] / r['n']:.2f}" if r["n"] else "-"
        out.append(f"  {who:32}{r['n']:>12,}{ngp:>8}{f(r['avg']):>11}{f(r['min']):>9}{f(r['ok_min']):>11}")
    out.append("  'Lowest OK' = the smallest value the sensor still passed, so the pass line is at or just below it.")
    return out


RUN_MIN = 20          # this many NG in a row = the line stopped / empty frames, not real defects
NEAR = (70, 80)       # scores this close to the pass line are the sensor's unsure calls


def check_without_labels(repo, now: datetime, day: datetime | None) -> dict | None:
    """What can be judged without a person: normal-hour NG, NG in long runs, unsure scores, which tool rejects."""
    a, b = _window(now, 1, day)
    sql = text("SELECT timestamp, analysis_status, tools_json FROM inspection WHERE timestamp >= :a AND timestamp < :b"
               " AND analysis_status IN ('PASS', 'FAIL') ORDER BY timestamp, trigger_no, id")
    with repo.engine.connect() as c:
        rows = c.execute(sql, {"a": a.strftime("%Y-%m-%d %H:%M:%S"), "b": b.strftime("%Y-%m-%d %H:%M:%S")}).all()
    if not rows:
        return None
    hours: dict = {}
    res = {"n": len(rows), "ng": 0, "run_ng": 0, "runs": 0, "near": 0, "only": Counter(), "multi": 0,
           "from": a, "to": b}
    streak = 0

    def close(k):
        if k >= RUN_MIN:
            res["run_ng"] += k
            res["runs"] += 1

    for t, st, tj in rows:
        ng = st == "FAIL"
        h = hours.setdefault((t or "")[:13], [0, 0])
        h[0] += 1
        h[1] += ng
        res["ng"] += ng
        if ng:
            streak += 1
        else:
            close(streak)
            streak = 0
        try:
            tools = json.loads(tj or "[]")
        except ValueError:
            tools = []
        bad = []
        for x in tools:
            v = _num(str(x.get("value", "")))
            if isinstance(v, (int, float)) and NEAR[0] <= v < NEAR[1]:
                res["near"] += 1
                break
        for x in tools:
            if str(x.get("status")).upper() == "NG":
                bad.append(f"T{x.get('no')}")
        if len(bad) == 1:
            res["only"][bad[0]] += 1
        elif len(bad) > 1:
            res["multi"] += 1
    close(streak)
    normal = [v for v in hours.values() if v[0] and v[1] / v[0] <= 0.05]
    res["hours"], res["normal_hours"] = len(hours), len(normal)
    res["normal_n"], res["normal_ng"] = sum(v[0] for v in normal), sum(v[1] for v in normal)
    return res


def check_section(repo, now: datetime, day: datetime | None) -> list[str]:
    r = check_without_labels(repo, now, day)
    if not r:
        return ["  no inspections in that period"]
    out = [f"  {r['n']:,} pieces, NG {100 * r['ng'] / r['n']:.2f}%"]
    if r["normal_n"]:
        out.append(f"  Normal hours (NG <= 5%): {r['normal_hours']} of {r['hours']}, NG {100 * r['normal_ng'] / r['normal_n']:.2f}%"
                   f"  -> the sensor passes at least {100 - 100 * r['normal_ng'] / r['normal_n']:.1f}% correctly there")
    if r["ng"]:
        out.append(f"  NG in runs of {RUN_MIN}+ in a row: {r['run_ng']:,} ({100 * r['run_ng'] / r['ng']:.1f}% of NG, "
                   f"{r['runs']} runs) - likely line stop / empty frames, check those images")
        only = ", ".join(f"only {k} x{v:,}" for k, v in sorted(r["only"].items()))
        out.append(f"  Which tool rejects: {only}{', ' if only else ''}several tools x{r['multi']:,}")
    out.append(f"  Unsure calls (a score {NEAR[0]}-{NEAR[1] - 1}, near the pass line): {r['near']:,} "
               f"({100 * r['near'] / r['n']:.2f}%)")
    out.append("  (no person needed for this part; missed defects can only be measured with --sample/--labels)")
    return out


# ------------------------------------------------------------------
# labels and accuracy
# ------------------------------------------------------------------

def system_truth(sensor_result: str, tools: dict) -> str:
    """The system's suggestion: the sensor's answer when every tool agrees with it, else blank (= look closely).
    It goes in its own column; only the person's 'truth' column is used for accuracy."""
    st = {str(x.get("status")).upper() for x in tools.values()}
    if len(st) == 1 and st.pop() == sensor_result:
        return sensor_result
    return ""


def make_sample(repo, n: int, days: int, now: datetime, seed: int | None = None, base: Path | None = None,
                day: datetime | None = None) -> tuple[Path | None, int]:
    """Half sensor-OK, half sensor-NG, random, not yet labelled. Returns (file, rows)."""
    wa, wb = _window(now, days, day)
    a, b = wa.strftime("%Y-%m-%d %H:%M:%S"), wb.strftime("%Y-%m-%d %H:%M:%S")
    rng = random.Random(seed)
    picked: list = []
    with repo.engine.connect() as c:
        for status in ("PASS", "FAIL"):
            ids = [r[0] for r in c.execute(text(
                "SELECT i.id FROM inspection i LEFT JOIN sensor_label l ON l.uid = i.uid"
                " WHERE i.analysis_status = :s AND i.timestamp >= :a AND i.timestamp < :b AND l.uid IS NULL"
                " AND i.image_file IS NOT NULL"), {"s": status, "a": a, "b": b})]
            picked += rng.sample(ids, min(len(ids), n // 2 + (n % 2 if status == "PASS" else 0)))
        rows = []
        for i in range(0, len(picked), 500):
            chunk = picked[i:i + 500]
            q = text("SELECT uid, timestamp, camera_id, analysis_status, score, tools_json, image_file, program_no,"
                     " trigger_no, inspection_time_ms, folder FROM inspection"
                     f" WHERE id IN ({','.join(str(int(x)) for x in chunk)})")
            rows += list(c.execute(q))
    if not rows:
        return None, 0
    rows.sort(key=lambda r: (r[1] or "", r[0]))
    out = (base or BASE_DIR) / "reports"
    out.mkdir(exist_ok=True)
    path = out / f"label_sample_{now:%Y-%m-%d_%H%M}.csv"
    parsed = []
    tool_cols: dict[int, str] = {}
    for uid, ts, cam, st, score, tj, img, prog, trig, ms, folder in rows:
        try:
            tools = json.loads(tj or "[]")
        except ValueError:
            tools = []
        for x in tools:
            tool_cols.setdefault(int(x.get("no", 0)), x.get("name") or "")
        parsed.append((uid, ts, cam, prog, trig, ms, "OK" if st == "PASS" else "NG", score,
                       ",".join(f"T{x['no']}" for x in tools if str(x.get("status")).upper() == "NG"),
                       {int(x.get("no", 0)): x for x in tools}, img, folder))
    nos = sorted(tool_cols)
    head = HEADER + [c for n in nos for c in (f"tool{n}_status", f"tool{n}_value")] + TAIL
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(head)
        for *cols, tools, img, folder in parsed:
            extra = [v for n in nos for v in ((tools.get(n) or {}).get("status"), (tools.get(n) or {}).get("value"))]
            w.writerow(cols + extra + [img, folder, system_truth(cols[6], tools), ""])
    return path, len(rows)


def write_label_workbook(csv_path: Path, archive_dir: str) -> Path | None:
    """Same rows as the CSV, as Excel: a link that opens each image and an OK/NG drop-down in 'truth'."""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
        from openpyxl.worksheet.datavalidation import DataValidation
    except ImportError:
        return None
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.reader(fh))
    if not rows:
        return None
    head = rows[0]
    wb = Workbook()
    ws = wb.active
    ws.title = "label"
    ws.append(["open image"] + head)
    for c in ws[1]:
        c.font = Font(bold=True)
    fi, ii, ti = head.index("folder"), head.index("image_file"), head.index("truth")
    sep = "\\" if "\\" in archive_dir or ":" in archive_dir[:3] else "/"
    for r in rows[1:]:
        target = archive_dir.rstrip("/\\") + sep + r[fi].replace("/", sep) + sep + r[ii]
        ws.append([f'=HYPERLINK("{target}","open")'] + [(_num(v)) for v in r])
    col = chr(ord("A") + ti + 1)
    dv = DataValidation(type="list", formula1='"OK,NG"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f"{col}2:{col}{len(rows)}")
    for i in range(2, len(rows) + 1):
        ws[f"{col}{i}"].fill = PatternFill("solid", fgColor="FFF2CC")
    ws.freeze_panes = "B2"
    ws.auto_filter.ref = ws.dimensions
    out = csv_path.with_suffix(".xlsx")
    wb.save(out)
    return out


def _num(v: str):
    try:
        return int(v) if v.isdigit() else float(v) if v.replace(".", "", 1).isdigit() else v
    except ValueError:
        return v


def _read_rows(path: Path) -> list[dict]:
    if path.suffix.lower() == ".xlsx":
        from openpyxl import load_workbook
        ws = load_workbook(path, read_only=True, data_only=True).worksheets[0]
        it = ws.iter_rows(values_only=True)
        head = [str(h or "") for h in next(it, [])]
        return [{h: ("" if v is None else str(v)) for h, v in zip(head, r)} for r in it if any(v is not None for v in r)]
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def import_labels(repo, path: Path, now: datetime, stats: dict | None = None) -> tuple[int, int, list[str]]:
    """(saved, skipped_blank, problems). truth must be OK or NG; unknown uids are reported. Only the person's
    'truth' column counts; 'system_truth' is just compared (stats['vs_system'] = rows where they differ)."""
    saved = blank = 0
    problems: list[str] = []
    rows = _read_rows(path)
    if not rows or "uid" not in rows[0] or "truth" not in rows[0]:
        return 0, 0, ["the file needs the columns 'uid' and 'truth' (use the file from --sample)"]
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
            sys_t = (r.get("system_truth") or "").strip().upper()
            if stats is not None and sys_t and sys_t != truth:
                stats["vs_system"] = stats.get("vs_system", 0) + 1
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
            # NG = the positive class. Precision: of what it rejected, how much was really NG.
            # Recall: of all really-NG pieces (estimated for the whole period), how many it caught.
            true_ng = p_ng * cm[("NG", "NG")] / n_ng
            missed = p_ok * cm[("OK", "NG")] / n_ok
            res["precision"] = cm[("NG", "NG")] / n_ng
            res["recall"] = true_ng / (true_ng + missed) if true_ng + missed else None
            if res["recall"] is not None and res["precision"] + res["recall"]:
                res["f1"] = 2 * res["precision"] * res["recall"] / (res["precision"] + res["recall"])
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
        if acc.get("recall") is not None:
            out.append(f"  Precision (NG it rejected that were really NG): {100 * acc['precision']:.1f}%   "
                       f"Recall (real NG it caught): {100 * acc['recall']:.1f}%   F1: {100 * acc.get('f1', 0):.1f}%")
    if acc["fr_tools"]:
        out.append("  False rejects by failing tool: " + ", ".join(f"{k} x{v}" for k, v in acc["fr_tools"].most_common(5)))
    if acc["n_sensor_ng"] < 30 or acc["n_sensor_ok"] < 30:
        out.append("  ! fewer than 30 checked pieces on one side - the ranges above are wide; check more.")
    out.append("  (the % in brackets is the 95 % range; it narrows as more pieces are checked)")
    return out


ACC_HEADER = ["run_at", "labelled_from", "labelled_to", "checked", "sensor_ok", "sensor_ng",
              "TP_sensorNG_truthNG", "FP_sensorNG_truthOK", "FN_sensorOK_truthNG", "TN_sensorOK_truthOK",
              "accuracy_pct", "precision_pct", "recall_pct", "f1_pct", "false_reject_pct", "missed_defect_pct",
              "period_total"]


def export_accuracy(repo, out_dir: Path, now: datetime) -> Path | None:
    """Append one row of ready-made numbers to reports\\accuracy.csv (opens in Excel), one row per run."""
    acc = accuracy(repo)
    if not acc:
        return None
    cm = acc["cm"]

    def pct(v):
        return "" if v is None else round(100 * v, 2)

    row = [f"{now:%Y-%m-%d %H:%M}", (acc["from"] or "")[:19], (acc["to"] or "")[:19], acc["n"],
           acc["n_sensor_ok"], acc["n_sensor_ng"], cm[("NG", "NG")], cm[("NG", "OK")], cm[("OK", "NG")],
           cm[("OK", "OK")], pct(acc["weighted"]), pct(acc.get("precision")), pct(acc.get("recall")),
           pct(acc.get("f1")),
           pct(cm[("NG", "OK")] / acc["n_sensor_ng"]) if acc["n_sensor_ng"] else "",
           pct(cm[("OK", "NG")] / acc["n_sensor_ok"]) if acc["n_sensor_ok"] else "",
           sum(acc["pop"]) if acc.get("pop") else ""]
    path = out_dir / "accuracy.csv"
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8-sig" if new else "utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(ACC_HEADER)
        w.writerow(row)
    return path


# ------------------------------------------------------------------

def run(args: list[str], s, _running_pid=None, now: datetime | None = None, base: Path | None = None) -> int:
    from app.database.repository import DatabaseRepository

    opt: dict = {"days": 7, "day": None, "set": None, "sample": None, "labels": None}
    it = iter(args)
    try:
        for a in it:
            if a == "--days":
                opt["days"] = max(int(next(it)), 1)
            elif a == "--day":
                opt["day"] = datetime.strptime(next(it), "%Y-%m-%d")
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
        print("Bad option value. Examples: run sensor --days 7 | --day 2026-10-13 | --sample 200 | --labels file.csv | --set \"text\"")
        return 2

    now = now or datetime.now()
    repo = DatabaseRepository(s.database_path)
    try:
        if opt["set"]:
            add_note(repo, opt["set"], now)
            print(f"Written down: {now:%Y-%m-%d %H:%M}  {opt['set']}")
        if opt["sample"]:
            path, n = make_sample(repo, opt["sample"], max(opt["days"], 1), now, base=base, day=opt["day"])
            if not path:
                print("No inspections with images to sample in that period (try --days 30).")
                return 1
            from app.report_xlsx import ensure_openpyxl
            book = write_label_workbook(path, str(s.archive_dir)) if ensure_openpyxl() is None else None
            target = book or path
            print(f"Wrote {n} pieces to {target}\n'system_truth' = the system's answer (blank where the tools disagree). "
                  f"Look at each image ('open') and put your own OK or NG in 'truth' - only 'truth' is used for "
                  f"accuracy (blank = skip). Save, then: run sensor --labels \"{target}\"")
            return 0
        if opt["labels"]:
            if not opt["labels"].exists():
                print(f"File not found: {opt['labels']}")
                return 2
            if opt["labels"].suffix.lower() == ".xlsx":
                from app.report_xlsx import ensure_openpyxl
                err = ensure_openpyxl()
                if err:
                    print(f"Cannot read .xlsx ({err}). Save it as CSV in Excel (File > Save As > CSV UTF-8) and use "
                          "that file, or run: .venv\\Scripts\\python -m pip install openpyxl")
                    return 2
            stats: dict = {}
            saved, blank, problems = import_labels(repo, opt["labels"], now, stats)
            print(f"Imported {saved} answers from the 'truth' column ({blank} blank skipped).")
            if stats.get("vs_system"):
                print(f"  you answered differently from 'system_truth' on {stats['vs_system']} rows")
            for p in problems[:10]:
                print("  ! " + p)
            if problems and not saved:
                return 2
        day = opt["day"]
        span = f"on {day:%Y-%m-%d}" if day else f"last {opt['days']} days"
        lines = [f"Sensor report {now:%Y-%m-%d %H:%M}" + (f"  (day {day:%Y-%m-%d})" if day else ""), "",
                 "1. Changes written down on the sensor (parameters)"] + notes_section(repo, now) + [
                 "", f"2. Metrics per tool, {span}"] + metrics_section(repo, now, opt["days"], day) + [
                 "", f"3. Check without labels, {'on ' + format(day, '%Y-%m-%d') if day else 'last 24 hours'}"]
        lines += check_section(repo, now, day) + [
                 "", "4. Accuracy (against pieces checked by a person, all labels)"] + accuracy_section(repo)
        print("\n".join(lines))
        out = (base or BASE_DIR) / "reports"
        out.mkdir(exist_ok=True)
        (out / f"sensor-{(day or now):%Y-%m-%d}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        exported = export_accuracy(repo, out, now)
        if exported:
            print(f"\nAccuracy / Precision / Recall / F1 added to {exported} (one row per run, opens in Excel)")
        return 0
    finally:
        repo.dispose()
