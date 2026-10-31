"""Label tool: the system labels a sample, a person labels the same images, then TP/TN/FP/FN and the statistics.

    run labeltool                                   open http://127.0.0.1:8765 in the browser
    run labeltool reports\\label_sample_*.xlsx      also open these label files (answers are saved back into them)
    run labeltool --images D:\\image-data-keyence   the image folder (default: D:\\image-data-keyence, else the archive)
    run labeltool file.xlsx --text                  print the numbers only, no browser
    options: --port 8765  --no-browser  --out <folder for new label files> (default reports\\, or labels\\ next
             to this file when it runs on its own)  --pop-ok N --pop-ng N (real OK/NG totals -> estimated accuracy)
             --person-only (leave out rows whose filled_by starts with 'auto')

On the page:
 1. "create label file": picks N images at random from the chosen days (and hours) of the image folder, half the
    sensor said OK and half NG. The system's answer comes from each image's IV4 .txt (Total Status + every tool).
    It is written as label_sample_<time>.xlsx, same columns as `run sensor --sample`, 'truth' empty.
 2. a person looks at each image and presses OK or NG (keys 1 / 2); every answer is saved into the file at once.
    The system's answer is hidden unless ticked, so it does not steer the person.
 3. the numbers: NG is the positive class. TP = system NG, person NG; TN = both OK; FP = system NG, person OK
    (false reject); FN = system OK, person NG (missed defect). Accuracy, Precision, Recall, F1 with 95 % ranges,
    by day and by file, as in `run sensor --labels`. The sample has as many sensor-OK as sensor-NG pieces, so its
    accuracy is not the line's; type the real OK/NG totals to re-weight it like `run sensor` does.
Old label_sample files (filled in Excel) open the same way. Images are found by name too: 00351_13102026_051030
looks straight in 2026-10-13\\*\\05\\ (the folder is laid out <date>\\<OK|NG>\\<hour>\\<uid>\\<image>).

Needs only Python 3.10+ and openpyxl; this file can be copied and run on its own: python labeltool.py ...
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import sys
import threading
import time
import webbrowser
from collections import Counter, defaultdict
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp"}
STATUS_DIRS = ("NG", "OK", "UNKNOWN")
_NAME = re.compile(r"(\d{2})(\d{2})(20\d{2})_(\d{2})(\d{2})(\d{2})")   # ..._13102026_051030


def norm(v) -> str:
    """OK / NG / '' from what people type (ok, Good, pass, ng, fail ...)."""
    s = str(v or "").strip().upper()
    if s in {"OK", "GOOD", "G", "PASS", "O"}:
        return "OK"
    if s in {"NG", "NOGOOD", "NO GOOD", "FAIL", "N", "BAD"}:
        return "NG"
    return ""


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if not n:
        return 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


# ------------------------------------------------------------------
# reading files
# ------------------------------------------------------------------

def read_rows(data: bytes, name: str) -> list[dict]:
    """Rows of the first sheet (xlsx) or the CSV, as {header: text}. The 'open image' formula column is ignored."""
    if name.lower().endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        ws = wb["label"] if "label" in wb.sheetnames else wb.worksheets[0]
        it = ws.iter_rows(values_only=True)
        head = [str(h or "").strip() for h in next(it, [])]
        rows = [{h: ("" if v is None else str(v)) for h, v in zip(head, r) if h and h != "open image"}
                for r in it if any(v is not None for v in r)]
        wb.close()
    else:
        text_ = data.decode("utf-8-sig", errors="replace")
        rows = list(csv.DictReader(io.StringIO(text_)))
    for r in rows:
        r["_file"] = name
        for k, v in list(r.items()):            # 100.0 -> 100 (openpyxl gives floats for whole numbers)
            if isinstance(v, str) and v.endswith(".0") and v[:-2].lstrip("-").isdigit():
                r[k] = v[:-2]
    return rows


# ------------------------------------------------------------------
# numbers
# ------------------------------------------------------------------

def _pct(k: int, n: int) -> dict:
    lo, hi = wilson(k, n)
    return {"k": k, "n": n, "pct": (100 * k / n) if n else None, "lo": 100 * lo, "hi": 100 * hi}


def compute(rows: list[dict], pop_ok: int | None = None, pop_ng: int | None = None,
            person_only: bool = False) -> dict:
    """Confusion matrix of rows that have both sensor_result and truth, plus the rates `run sensor` reports."""
    cm: Counter = Counter()
    fr_tools: Counter = Counter()
    by_day: dict = defaultdict(Counter)
    by_file: dict = defaultdict(Counter)
    blank = bad = auto_skipped = vs_system = 0
    ts: list[str] = []
    for r in rows:
        if person_only and str(r.get("filled_by") or "").strip().lower().startswith("auto"):
            auto_skipped += 1
            continue
        s, t = norm(r.get("sensor_result")), norm(r.get("truth"))
        if not str(r.get("truth") or "").strip():
            blank += 1
            continue
        if not s or not t:
            bad += 1
            continue
        cm[(s, t)] += 1
        day = str(r.get("timestamp") or "")[:10] or "-"
        by_day[day][(s, t)] += 1
        by_file[r.get("_file", "-")][(s, t)] += 1
        if r.get("timestamp"):
            ts.append(str(r["timestamp"]))
        sys_t = norm(r.get("system_truth"))
        if sys_t and sys_t != t:
            vs_system += 1
        if s == "NG" and t == "OK":
            for tool in str(r.get("failing_tools") or "").replace(" ", "").split(","):
                if tool:
                    fr_tools[tool] += 1
    tp, fp, fn, tn = cm[("NG", "NG")], cm[("NG", "OK")], cm[("OK", "NG")], cm[("OK", "OK")]
    n = tp + fp + fn + tn
    n_ok, n_ng = tn + fn, tp + fp
    res = {"rows": len(rows), "checked": n, "blank": blank, "bad": bad, "auto_skipped": auto_skipped,
           "vs_system": vs_system, "tp": tp, "fp": fp, "fn": fn, "tn": tn, "sensor_ok": n_ok, "sensor_ng": n_ng,
           "from": min(ts) if ts else None, "to": max(ts) if ts else None,
           "accuracy_sample": _pct(tp + tn, n),
           "false_reject": _pct(fp, n_ng),        # sensor NG, truth OK: of the pieces it rejected
           "missed_defect": _pct(fn, n_ok),       # sensor OK, truth NG: of the pieces it passed
           "precision": _pct(tp, n_ng),
           "recall_sample": _pct(tp, tp + fn),
           "f1_sample": (200 * tp / (2 * tp + fp + fn)) if (2 * tp + fp + fn) else None,
           "fr_tools": fr_tools.most_common(),
           "by_day": [_small(k, v) for k, v in sorted(by_day.items())],
           "by_file": [_small(k, v) for k, v in sorted(by_file.items())],
           "weighted": None, "few": bool(n) and (n_ok < 30 or n_ng < 30)}
    if pop_ok is not None and pop_ng is not None and pop_ok + pop_ng and n_ok and n_ng:
        # same as sensor.accuracy(): each side's error rate applied to the real number of OK/NG pieces
        wrong = pop_ok * fn / n_ok + pop_ng * fp / n_ng
        true_ng = pop_ng * tp / n_ng
        missed = pop_ok * fn / n_ok
        recall = true_ng / (true_ng + missed) if true_ng + missed else None
        prec = tp / n_ng
        res["weighted"] = {"pop_ok": pop_ok, "pop_ng": pop_ng, "accuracy": 100 * (1 - wrong / (pop_ok + pop_ng)),
                           "recall": None if recall is None else 100 * recall,
                           "f1": (200 * prec * recall / (prec + recall)) if recall is not None and prec + recall else None,
                           "est_false_reject": pop_ng * fp / n_ng, "est_missed": missed}
    return res


def _small(name: str, c: Counter) -> dict:
    tp, fp, fn, tn = c[("NG", "NG")], c[("NG", "OK")], c[("OK", "NG")], c[("OK", "OK")]
    n = tp + fp + fn + tn
    return {"name": name, "n": n, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "accuracy": (100 * (tp + tn) / n) if n else None,
            "false_reject": (100 * fp / (tp + fp)) if tp + fp else None,
            "missed_defect": (100 * fn / (tn + fn)) if tn + fn else None}


def _f(p: dict | float | None) -> str:
    if isinstance(p, dict):
        return "-" if p["pct"] is None else f"{p['pct']:.1f}% ({p['lo']:.1f}-{p['hi']:.1f}%)  [{p['k']}/{p['n']}]"
    return "-" if p is None else f"{p:.2f}%"


def text_report(st: dict) -> str:
    out = [f"rows {st['rows']:,}   checked (truth OK/NG) {st['checked']:,}   blank truth {st['blank']:,}"
           + (f"   unreadable {st['bad']}" if st["bad"] else "")
           + (f"   left out (auto-filled) {st['auto_skipped']}" if st["auto_skipped"] else ""),
           f"period {st['from'] or '-'} to {st['to'] or '-'}",
           "                      truth OK   truth NG",
           f"  sensor said OK   {st['tn']:>10,} {st['fn']:>10,}",
           f"  sensor said NG   {st['fp']:>10,} {st['tp']:>10,}",
           f"Accuracy (this sample)          {_f(st['accuracy_sample'])}",
           f"False reject (sensor NG, OK)    {_f(st['false_reject'])}   of the pieces it rejected",
           f"Missed defect (sensor OK, NG)   {_f(st['missed_defect'])}   of the pieces it passed",
           f"Precision                       {_f(st['precision'])}",
           f"Recall (this sample)            {_f(st['recall_sample'])}",
           f"F1 (this sample)                {_f(st['f1_sample'])}"]
    w = st["weighted"]
    if w:
        out.append(f"Estimated on all {w['pop_ok'] + w['pop_ng']:,} pieces (OK {w['pop_ok']:,} / NG {w['pop_ng']:,}):"
                   f" accuracy {_f(w['accuracy'])}  recall {_f(w['recall'])}  F1 {_f(w['f1'])}")
    else:
        out.append("(half of the sample is sensor-NG, so 'this sample' is not the line's accuracy:"
                   " add --pop-ok/--pop-ng for the estimate on all pieces)")
    if st["fr_tools"]:
        out.append("False rejects by failing tool: " + ", ".join(f"{k} x{v}" for k, v in st["fr_tools"]))
    if st["vs_system"]:
        out.append(f"truth differs from system_truth on {st['vs_system']} rows")
    if st["few"]:
        out.append("! fewer than 30 checked pieces on one side - the ranges are wide; check more.")
    if len(st["by_day"]) > 1:
        out.append("by day:  day         n    acc%   false-reject%  missed%")
        for d in st["by_day"]:
            out.append(f"         {d['name']:10} {d['n']:>4}  {_f(d['accuracy']):>7}  {_f(d['false_reject']):>12}"
                       f"  {_f(d['missed_defect']):>7}")
    return "\n".join(out)


def stats_workbook(st: dict, rows: list[dict]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    ws = wb.active
    ws.title = "summary"
    for line in text_report(st).splitlines():
        ws.append([line])
    ws.append([])
    ws.append(["metric", "value_pct", "low_95", "high_95", "k", "n"])
    for key in ("accuracy_sample", "false_reject", "missed_defect", "precision", "recall_sample"):
        p = st[key]
        ws.append([key, p["pct"], p["lo"], p["hi"], p["k"], p["n"]])
    ws.append(["f1_sample", st["f1_sample"]])
    if st["weighted"]:
        for k, v in st["weighted"].items():
            ws.append([f"estimated_{k}", v])
    ws.column_dimensions["A"].width = 70
    for name, key in (("by_day", "by_day"), ("by_file", "by_file")):
        s = wb.create_sheet(name)
        s.append(["name", "n", "TP_sensorNG_truthNG", "FP_sensorNG_truthOK", "FN_sensorOK_truthNG",
                  "TN_sensorOK_truthOK", "accuracy_pct", "false_reject_pct", "missed_defect_pct"])
        for d in st[key]:
            s.append([d["name"], d["n"], d["tp"], d["fp"], d["fn"], d["tn"], d["accuracy"], d["false_reject"],
                      d["missed_defect"]])
    s = wb.create_sheet("wrong")
    head = ["kind", "uid", "timestamp", "sensor_result", "truth", "score", "failing_tools", "image_file", "folder",
            "file"]
    s.append(head)
    for r in rows:
        k = kind(r)
        if k in {"FP", "FN"}:
            s.append([k] + [r.get(h, "") for h in head[1:-1]] + [r.get("_file", "")])
    for sh in wb.worksheets:
        for c in sh[1]:
            c.font = Font(bold=True)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def kind(r: dict) -> str:
    s, t = norm(r.get("sensor_result")), norm(r.get("truth"))
    if not s or not t:
        return "blank"
    return {("NG", "NG"): "TP", ("NG", "OK"): "FP", ("OK", "NG"): "FN", ("OK", "OK"): "TN"}[(s, t)]


# ------------------------------------------------------------------
# images
# ------------------------------------------------------------------

class Images:
    """Finds images under root; never serves anything outside it."""

    def __init__(self, root: Path | None):
        self.root = root.resolve() if root else None

    def inside(self, p: Path) -> bool:
        if not self.root:
            return False
        try:
            p.resolve().relative_to(self.root)
            return True
        except (ValueError, OSError):
            return False

    def rel(self, p: Path) -> str:
        return p.resolve().relative_to(self.root).as_posix()

    def for_row(self, r: dict) -> str | None:
        """root/<folder>/<image_file>, or a search by the image name when the folder moved."""
        if not self.root:
            return None
        img, folder = str(r.get("image_file") or ""), str(r.get("folder") or "")
        if img and folder:
            p = self.root / folder.replace("\\", "/") / img
            if p.is_file() and self.inside(p):
                return self.rel(p)
        hits = self.by_name(Path(img).stem or str(r.get("uid") or ""), limit=1)
        return hits[0] if hits else None

    def _hour_dirs(self, q: str) -> list[Path] | None:
        """The folders a name like 00351_13102026_051030 can be in; None when the name has no date."""
        m = _NAME.search(q)
        if not m:
            return None
        dd, mm, yyyy, hh = m.group(1), m.group(2), m.group(3), m.group(4)
        day = self.root / f"{yyyy}-{mm}-{dd}"
        return [day / s / hh for s in STATUS_DIRS if (day / s / hh).is_dir()]

    def by_name(self, q: str, limit: int = 60, seconds: float = 20.0) -> list[str]:
        """Images whose file name (or uid folder) contains q, case-insensitive."""
        if not self.root or not q:
            return []
        ql = q.lower().strip()
        out: list[str] = []
        dirs = self._hour_dirs(q)
        if dirs is not None:
            for d in dirs:
                self._scan(d, ql, out, limit, time.monotonic() + seconds)
            if out:
                return out
        self._scan(self.root, ql, out, limit, time.monotonic() + seconds)
        return out

    def _scan(self, top: Path, ql: str, out: list[str], limit: int, deadline: float) -> None:
        for dirpath, dirnames, filenames in os.walk(top):
            dirnames.sort(reverse=True)                       # newest dates first
            in_dir = ql in os.path.basename(dirpath).lower()
            for f in filenames:
                if Path(f).suffix.lower() in IMAGE_EXT and (in_dir or ql in f.lower()):
                    rel = self.rel(Path(dirpath) / f)
                    if rel not in out:
                        out.append(rel)
                    if len(out) >= limit:
                        return
            if time.monotonic() > deadline:
                return


# ------------------------------------------------------------------
# system labels: a new label file from the image folder (no database)
# ------------------------------------------------------------------

def read_txt(p: Path) -> dict:
    """The IV4 result text next to each image: total status, program, trigger, time and every tool."""
    raw = p.read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff") or raw[1:2] == b"\x00":
        text_ = raw.decode("utf-16", errors="replace")
    else:
        try:
            text_ = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text_ = raw.decode("cp874", errors="replace")
    out: dict = {"tools": []}
    for line in text_.splitlines():
        parts = [x.strip() for x in line.split("\t")]
        key = parts[0].lower()
        val = parts[1] if len(parts) > 1 else ""
        if key.startswith("time and date"):
            out["time"] = parts[2] if len(parts) > 2 else ""
        elif key.startswith("program no"):
            out["program"] = val
        elif key.startswith("trigger no"):
            out["trigger_no"] = val
        elif key.startswith("time[ms]"):
            out["time_ms"] = val
        elif key.startswith("total status"):
            out["status"] = norm(val)
        else:
            m = re.match(r"tool0*(\d+)\s*:?\s*(.*)", parts[0], re.I)
            if m:
                out["tools"].append({"no": int(m.group(1)), "name": m.group(2), "status": norm(val),
                                     "value": parts[2] if len(parts) > 2 else ""})
    return out


def _hour_ok(h: str, hours: tuple[int, int] | None) -> bool:
    if not hours:
        return True
    try:
        x = int(h)
    except ValueError:
        return False
    a, b = hours
    return a <= x < b if a < b else (x >= a or x < b)


def days_in(root: Path | None) -> list[str]:
    if not root or not root.is_dir():
        return []
    return sorted((e.name for e in os.scandir(root) if e.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", e.name)),
                  reverse=True)


def make_sample(root: Path, days: list[str], n: int, hours: tuple[int, int] | None = None,
                seed: int | None = None) -> tuple[list[dict], list[str]]:
    """Half sensor-OK, half sensor-NG pieces picked at random from <day>\\<OK|NG>\\<hour>\\<uid>\\.
    Each row carries the system's answer (sensor_result, tools) and an empty 'truth' for the person.
    Returns (rows, head). Only the picked pieces' text files are read."""
    import random
    pools: dict[str, list[Path]] = {"OK": [], "NG": []}
    for d in days:
        for st in pools:
            sd = root / d / st
            if not sd.is_dir():
                continue
            for h in sorted(os.listdir(sd)):
                hd = sd / h
                if _hour_ok(h, hours) and hd.is_dir():
                    pools[st] += [Path(e.path) for e in os.scandir(hd) if e.is_dir()]
    rng = random.Random(seed)
    picked: list[tuple[str, Path]] = []
    want = {"OK": n // 2 + n % 2, "NG": n // 2}
    short = {s: max(0, want[s] - len(pools[s])) for s in pools}  # one side short -> fill from the other
    want["OK"] += short["NG"]
    want["NG"] += short["OK"]
    for st in pools:
        picked += [(st, p) for p in rng.sample(pools[st], min(want[st], len(pools[st])))]
    rows: list[dict] = []
    tool_nos: set[int] = set()
    for st, d in picked:
        files = sorted(os.listdir(d))
        img = next((f for f in files if Path(f).suffix.lower() in IMAGE_EXT), "")
        txt = next((f for f in files if f.lower().endswith(".txt")), "")
        info = read_txt(d / txt) if txt else {"tools": []}
        sensor = ""
        if "manifest.json" in files:
            try:
                sensor = json.loads((d / "manifest.json").read_text(encoding="utf-8")).get("sensor_id") or ""
            except (OSError, ValueError):
                pass
        result = info.get("status") or st
        tools = info["tools"]
        vals = []
        for t in tools:
            tool_nos.add(t["no"])
            try:
                vals.append(float(t["value"]))
            except (TypeError, ValueError):
                pass
        day = d.parent.parent.parent.name
        r = {"uid": d.name, "timestamp": f"{day} {info.get('time', '')}".strip(), "sensor": sensor,
             "program": info.get("program", ""), "trigger_no": info.get("trigger_no", ""),
             "time_ms": info.get("time_ms", ""), "sensor_result": result,
             "score": f"{min(vals):g}" if vals else "",
             "failing_tools": ",".join(f"T{t['no']}" for t in tools if t["status"] == "NG")}
        for t in tools:
            r[f"tool{t['no']}_status"], r[f"tool{t['no']}_value"] = t["status"], t["value"]
        r["image_file"] = img
        r["folder"] = d.relative_to(root).as_posix()
        st_set = {t["status"] for t in tools}
        r["system_truth"] = result if len(st_set) == 1 and st_set.pop() == result else ""
        r["truth"] = ""
        rows.append(r)
    rows.sort(key=lambda r: (r["timestamp"], r["uid"]))
    nos = sorted(tool_nos)
    head = (["uid", "timestamp", "sensor", "program", "trigger_no", "time_ms", "sensor_result", "score",
             "failing_tools"] + [c for k in nos for c in (f"tool{k}_status", f"tool{k}_value")]
            + ["image_file", "folder", "system_truth", "truth"])
    for r in rows:
        for h in head:
            r.setdefault(h, "")
    return rows, head


def write_book(path: Path, rows: list[dict], head: list[str], root: Path | None) -> None:
    """The label_sample layout: an 'open' link to each image, the columns, an OK/NG drop-down in 'truth'."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation
    wb = Workbook()
    ws = wb.active
    ws.title = "label"
    ws.append(["open image"] + head)
    for c in ws[1]:
        c.font = Font(bold=True)
    for r in rows:
        link = ""
        if root and r.get("folder") and r.get("image_file"):
            target = str(root / str(r["folder"]).replace("\\", "/") / str(r["image_file"]))
            link = f'=HYPERLINK("{target}","open")'
        ws.append([link] + [_cell(r.get(h, "")) for h in head])
    if "truth" in head and rows:
        col = ws.cell(row=1, column=head.index("truth") + 2).column_letter
        dv = DataValidation(type="list", formula1='"OK,NG"', allow_blank=True)
        ws.add_data_validation(dv)
        dv.add(f"{col}2:{col}{len(rows) + 1}")
        for i in range(2, len(rows) + 2):
            ws[f"{col}{i}"].fill = PatternFill("solid", fgColor="FFF2CC")
    ws.freeze_panes = "B2"
    ws.auto_filter.ref = ws.dimensions
    tmp = path.with_name(path.name + ".tmp")
    wb.save(tmp)
    os.replace(tmp, path)            # never leaves a half-written file


def _cell(v):
    s = str(v)
    if s.isdigit() and not (len(s) > 1 and s[0] == "0"):
        return int(s)
    try:
        return float(s) if re.fullmatch(r"-?\d+\.\d+", s) else s
    except ValueError:
        return s


# ------------------------------------------------------------------
# web page
# ------------------------------------------------------------------

class LabelFile:
    def __init__(self, path: Path | None, rows: list[dict], head: list[str]):
        self.path, self.rows, self.head = path, rows, head
        self.error = ""


class State:
    def __init__(self, images: Images, out_dir: Path | None = None):
        self.images = images
        self.out_dir = out_dir
        self.files: dict[str, LabelFile] = {}
        self.lock = threading.Lock()

    def rows(self) -> list[dict]:
        with self.lock:
            return [r for f in self.files.values() for r in f.rows]

    def add(self, name: str, data: bytes, path: Path | None = None) -> int:
        """A file opened from disk is saved back to itself; one chosen in the browser is kept as a copy in out_dir."""
        rows = read_rows(data, name)
        head = [h for h in (rows[0].keys() if rows else []) if h != "_file"]
        if rows and "truth" not in head:
            head.append("truth")
        if path is None and self.out_dir:
            self.out_dir.mkdir(parents=True, exist_ok=True)
            path = self.out_dir / name
            path.write_bytes(data)
        with self.lock:
            self.files[name] = LabelFile(path, rows, head)
        return len(rows)

    def new_sample(self, days: list[str], n: int, hours: tuple[int, int] | None, now: datetime) -> tuple[str, int]:
        if not self.images.root:
            raise ValueError("no image folder (start with --images)")
        rows, head = make_sample(self.images.root, days, n, hours)
        if not rows:
            raise ValueError("no images found for those days/hours")
        out = self.out_dir or Path.cwd()
        out.mkdir(parents=True, exist_ok=True)
        name = f"label_sample_{now:%Y-%m-%d_%H%M%S}.xlsx"
        for r in rows:
            r["_file"] = name
        write_book(out / name, rows, head, self.images.root)
        with self.lock:
            self.files[name] = LabelFile(out / name, rows, head)
        return name, len(rows)

    def set_truth(self, name: str, uid: str, truth: str) -> str:
        """Saves at once. Returns '' or the reason the file could not be written (e.g. open in Excel)."""
        with self.lock:
            f = self.files.get(name)
            if not f:
                return "file not loaded"
            for r in f.rows:
                if r.get("uid") == uid:
                    r["truth"] = truth
                    if "filled_by" in r:
                        r["filled_by"] = "person" if truth else ""
                    break
            else:
                return "uid not in the file"
            if not f.path:
                return ""
            try:
                if f.path.suffix.lower() == ".csv":
                    with open(f.path, "w", newline="", encoding="utf-8-sig") as fh:
                        w = csv.DictWriter(fh, fieldnames=f.head, extrasaction="ignore")
                        w.writeheader()
                        w.writerows(f.rows)
                else:
                    write_book(f.path, f.rows, f.head, self.images.root)
                f.error = ""
            except OSError as exc:
                f.error = f"cannot save {f.path.name} (close it in Excel?): {exc}"
            return f.error


def _int(v) -> int | None:
    try:
        return int(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _row_out(r: dict, images: Images) -> dict:
    keys = ("uid", "timestamp", "sensor", "sensor_result", "truth", "system_truth", "filled_by", "score",
            "failing_tools", "image_file", "folder")
    d = {k: r.get(k, "") for k in keys if k in r}
    d["tools"] = " ".join(f"{k[:-7].replace('tool', 'T')} {r[k]} {r.get(k[:-7] + '_value', '')}"
                          for k in r if re.fullmatch(r"tool\d+_status", k) and r[k])
    d["file"] = r.get("_file", "")
    d["kind"] = kind(r)
    d["img"] = images.for_row(r)
    return d


def make_handler(state: State):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, body: bytes, ctype: str, code: int = 200, extra: dict | None = None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200):
            self._send(json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", code)

        def _stats(self, q: dict) -> dict:
            return compute(state.rows(), _int(q.get("pop_ok", [""])[0]), _int(q.get("pop_ng", [""])[0]),
                           q.get("person_only", ["0"])[0] == "1")

        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path == "/":
                return self._send(PAGE.encode("utf-8"), "text/html; charset=utf-8")
            if u.path == "/api/state":
                st = self._stats(q)
                files = {k: {"rows": len(f.rows), "done": sum(1 for r in f.rows if norm(r.get("truth"))),
                             "path": str(f.path or ""), "error": f.error} for k, f in state.files.items()}
                return self._json({"files": files, "root": str(state.images.root or ""), "stats": st,
                                   "text": text_report(st), "days": days_in(state.images.root)})
            if u.path == "/api/rows":
                want = q.get("kind", ["FP"])[0]
                fname = q.get("file", [""])[0]
                if fname:
                    f = state.files.get(fname)
                    rows = list(f.rows) if f else []
                else:
                    rows = [r for r in state.rows() if want == "all" or kind(r) == want][:300]
                return self._json([_row_out(r, state.images) for r in rows])
            if u.path == "/api/search":
                term = (q.get("q", [""])[0]).strip()
                if not term:
                    return self._json({"rows": [], "files": []})
                tl = term.lower()
                rows = [r for r in state.rows() if tl in str(r.get("image_file", "")).lower()
                        or tl in str(r.get("uid", "")).lower()][:100]
                found = [_row_out(r, state.images) for r in rows]
                known = {d["img"] for d in found if d["img"]}
                extra = [p for p in state.images.by_name(term) if p not in known]
                return self._json({"rows": found, "files": extra})
            if u.path == "/img":
                rel = q.get("p", [""])[0]
                p = (state.images.root / rel) if state.images.root else None
                if not p or not state.images.inside(p) or not p.is_file() or p.suffix.lower() not in IMAGE_EXT:
                    return self._send(b"not found", "text/plain", 404)
                ctype = {".png": "image/png", ".bmp": "image/bmp"}.get(p.suffix.lower(), "image/jpeg")
                return self._send(p.read_bytes(), ctype, extra={"Cache-Control": "max-age=3600"})
            if u.path == "/api/export":
                body = stats_workbook(self._stats(q), state.rows())
                return self._send(body, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                  extra={"Content-Disposition": 'attachment; filename="label_accuracy.xlsx"'})
            return self._send(b"not found", "text/plain", 404)

        def do_POST(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            arg = lambda k: q.get(k, [""])[0]  # noqa: E731
            if u.path == "/api/load":
                name = Path(arg("name") or "file.xlsx").name
                data = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                try:
                    n = state.add(name, data)
                except Exception as exc:  # noqa: BLE001 - shown on the page
                    return self._json({"error": f"{name}: {exc}"}, 400)
                return self._json({"ok": True, "rows": n})
            if u.path == "/api/remove":
                with state.lock:
                    state.files.pop(arg("name"), None)
                return self._json({"ok": True})
            if u.path == "/api/sample":
                days = [d for d in arg("days").split(",") if d]
                h1, h2 = _int(arg("h1")), _int(arg("h2"))
                hours = (h1, h2) if h1 is not None and h2 is not None and h1 != h2 else None
                if not days:
                    return self._json({"error": "choose at least one day"}, 400)
                try:
                    name, n = state.new_sample(days, _int(arg("n")) or 200, hours, datetime.now())
                except (ValueError, OSError) as exc:
                    return self._json({"error": str(exc)}, 400)
                return self._json({"ok": True, "name": name, "rows": n})
            if u.path == "/api/label":
                t = norm(arg("truth"))
                err = state.set_truth(arg("file"), arg("uid"), t)
                return self._json({"ok": not err, "error": err})
            return self._send(b"not found", "text/plain", 404)

    return H


PAGE = r"""<!DOCTYPE html><html lang="th"><head><meta charset="utf-8"><title>IV4 Label Accuracy</title>
<style>
body{font-family:Segoe UI,Tahoma,sans-serif;margin:0;background:#f5f6f8;color:#222}
header{background:#1f3a5f;color:#fff;padding:10px 18px;font-size:18px}
main{display:grid;grid-template-columns:430px 1fr;gap:14px;padding:14px}
.card{background:#fff;border-radius:8px;padding:12px 14px;box-shadow:0 1px 3px #0002;margin-bottom:14px}
h2{font-size:15px;margin:0 0 8px}
table{border-collapse:collapse;font-size:13px;width:100%}td,th{border:1px solid #ddd;padding:4px 6px;text-align:right}
th{background:#eef1f5}td:first-child,th:first-child{text-align:left}
.big{font-size:26px;font-weight:600}.muted{color:#777;font-size:12px}
input[type=text],input[type=number],select{padding:6px;border:1px solid #bbb;border-radius:4px}
button{padding:6px 12px;border:0;border-radius:4px;background:#1f3a5f;color:#fff;cursor:pointer}
button.sec{background:#e3e7ee;color:#222}button.ok{background:#27ae60;font-size:18px;padding:10px 26px}
button.ng{background:#c0392b;font-size:18px;padding:10px 26px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:10px}
.tile{background:#fff;border:1px solid #ddd;border-radius:6px;overflow:hidden;cursor:pointer;font-size:12px}
.tile img{width:100%;height:160px;object-fit:contain;background:#111}.tile div{padding:5px}
.FP{color:#c0392b;font-weight:600}.FN{color:#d35400;font-weight:600}.TP,.TN{color:#27ae60}
#view{position:fixed;inset:0;background:#000d;display:none;align-items:center;justify-content:center;z-index:9;flex-direction:column}
#view img{max-width:95vw;max-height:85vh}#view div{color:#fff;margin-top:8px;font-size:14px}
.kpi{display:grid;grid-template-columns:1fr 1fr;gap:8px}.kpi>div{background:#f7f9fc;border-radius:6px;padding:8px}
.tabs button{margin-right:4px}.tabs button.on{background:#1f3a5f;color:#fff}
#days{max-height:120px;overflow:auto;border:1px solid #ddd;padding:4px;font-size:13px}
#limg{width:100%;max-height:62vh;object-fit:contain;background:#111;border-radius:6px}
.err{color:#c0392b}
</style></head><body>
<header>IV4 Label Accuracy - ระบบสร้าง label / คนตรวจ / คำนวณ TP TN FP FN</header>
<main><section>
<div class="card"><h2>1. สร้างไฟล์ label ใหม่จากโฟลเดอร์รูป (คำตอบของระบบ)</h2>
<div class="muted">สุ่มรูปครึ่งหนึ่งจากที่ sensor บอก OK อีกครึ่งจากที่บอก NG ผลของระบบอ่านจากไฟล์ .txt ของแต่ละรูป</div>
<div>วันที่ <a href="#" onclick="alld(1);return false">เลือกทั้งหมด</a> · <a href="#" onclick="alld(0);return false">ไม่เลือก</a></div>
<div id="days"></div>
<div style="margin-top:6px">จำนวน <input type="number" id="sn" value="200" style="width:70px">
ชั่วโมง <input type="number" id="h1" min="0" max="23" style="width:50px"> ถึง <input type="number" id="h2" min="0" max="24" style="width:50px"> <span class="muted">(ว่าง = ทั้งวัน)</span></div>
<div style="margin-top:6px"><button onclick="mk()">สร้างไฟล์ label</button> <span id="mkmsg" class="muted"></span></div></div>
<div class="card"><h2>2. ไฟล์ label ที่เปิดอยู่</h2>
<input type="file" id="f" multiple accept=".xlsx,.csv"> <span class="muted">(เปิดไฟล์ label_sample เดิมได้)</span><div id="files" class="muted"></div></div>
<div class="card"><h2>4. ตัวเลือกการคำนวณ</h2>
<div>จำนวนจริงทั้งช่วงเวลา (ถ้ามี)<br>OK <input type="number" id="pok" style="width:110px"> NG <input type="number" id="png" style="width:90px"></div>
<label><input type="checkbox" id="po"> ไม่นับแถวที่ระบบเติมเอง (filled_by = auto...)</label>
<div style="margin-top:6px"><button onclick="load()">คำนวณใหม่</button> <button class="sec" onclick="exp()">ดาวน์โหลด Excel สรุป</button></div></div>
<div class="card" id="stats"></div>
</section><section>
<div class="card tabs"><button id="t1" class="on" onclick="tab(1)">3. คนตรวจรูป (กรอก truth)</button><button id="t2" class="sec" onclick="tab(2)">ค้นหารูป / ดูแถวที่ผิด</button></div>
<div id="p1"><div class="card">
<div>ไฟล์ <select id="lf" onchange="openf()"></select>
<label><input type="checkbox" id="todo" checked onchange="openf()"> เฉพาะที่ยังไม่ตรวจ</label>
<label><input type="checkbox" id="showsys" onchange="draw()"> แสดงคำตอบของระบบ</label></div>
<div class="muted">ดูรูปแล้วกด OK / NG (คีย์ลัด 1 = OK, 2 = NG, 0 = ล้าง, ← → เลื่อน) บันทึกลงไฟล์ทันที ปิดไฟล์นั้นใน Excel ไว้ระหว่างตรวจ</div>
<div id="lbl" style="margin-top:8px"></div></div></div>
<div id="p2" style="display:none"><div class="card">
<input type="text" id="q" style="width:60%" placeholder="เช่น 00351_13102026_051030 หรือ uid"> <button onclick="search()">ค้นหา</button>
<span class="muted">โฟลเดอร์รูป: <span id="root"></span></span>
<div style="margin-top:8px">แสดงแถว: <button class="sec" onclick="rows('FP')">ระบบ NG แต่คนว่า OK (FP)</button>
<button class="sec" onclick="rows('FN')">ระบบ OK แต่คนว่า NG (FN)</button> <button class="sec" onclick="rows('all')">ทั้งหมด</button></div></div>
<div id="res" class="grid"></div></div>
</section></main>
<div id="view" onclick="this.style.display='none'"><img id="vimg"><div id="vtxt"></div></div>
<script>
const $=id=>document.getElementById(id);
const pc=p=>p==null?'-':(typeof p=='number'?p.toFixed(2)+'%':(p.pct==null?'-':p.pct.toFixed(1)+'% <span class=muted>('+p.lo.toFixed(1)+'-'+p.hi.toFixed(1)+'%) '+p.k+'/'+p.n+'</span>'));
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let L={file:'',rows:[],i:0},daysDone=false;
function qs(){return 'pop_ok='+($('pok').value||'')+'&pop_ng='+($('png').value||'')+'&person_only='+($('po').checked?1:0)}
function tab(n){$('p1').style.display=n==1?'':'none';$('p2').style.display=n==2?'':'none';$('t1').className=n==1?'on':'sec';$('t2').className=n==2?'on':'sec'}
$('f').onchange=async e=>{let last='';for(const f of e.target.files){const r=await fetch('/api/load?name='+encodeURIComponent(f.name),{method:'POST',body:await f.arrayBuffer()});
 const j=await r.json(); if(j.error) alert(j.error); else last=f.name;} e.target.value=''; await load(); if(last){$('lf').value=last;openf()}};
async function rm(n){await fetch('/api/remove?name='+encodeURIComponent(n),{method:'POST'});await load();openf()}
function alld(v){document.querySelectorAll('#days input').forEach(x=>x.checked=!!v)}
async function mk(){const days=[...document.querySelectorAll('#days input:checked')].map(x=>x.value);
 if(!days.length){alert('เลือกวันที่ก่อน');return} $('mkmsg').textContent='กำลังสุ่มรูป...';
 const j=await (await fetch('/api/sample?days='+days.join(',')+'&n='+($('sn').value||200)+'&h1='+$('h1').value+'&h2='+$('h2').value,{method:'POST'})).json();
 if(j.error){$('mkmsg').textContent='';alert(j.error);return}
 $('mkmsg').textContent='สร้าง '+j.name+' ('+j.rows+' รูป)'; await load(); $('lf').value=j.name; tab(1); openf()}
async function load(){const j=await (await fetch('/api/state?'+qs())).json(); $('root').textContent=j.root||'(ไม่ได้ตั้ง)';
 if(!daysDone){$('days').innerHTML=j.days.map(d=>'<label><input type=checkbox value="'+d+'"> '+d+'</label><br>').join('')||'<span class=muted>ไม่พบโฟลเดอร์วันที่</span>';daysDone=true}
 const names=Object.keys(j.files);
 $('files').innerHTML=names.map(k=>{const f=j.files[k];return '<b>'+esc(k)+'</b> ตรวจแล้ว '+f.done+'/'+f.rows+' <a href="#" data-n="'+esc(k)+'" onclick="rm(this.dataset.n);return false">ปิด</a><br><span class=muted>'+esc(f.path)+'</span>'+(f.error?'<br><span class=err>'+esc(f.error)+'</span>':'')}).join('<br>')||'ยังไม่มีไฟล์';
 const cur=$('lf').value; $('lf').innerHTML=names.map(k=>'<option>'+esc(k)+'</option>').join(''); if(names.includes(cur))$('lf').value=cur;
 const s=j.stats; if(!s.checked){$('stats').innerHTML='<h2>5. ผล</h2>ยังไม่มีแถวที่คนกรอก truth (OK/NG)';return}
 let h='<h2>5. ผล ('+s.checked+' ชิ้นที่คนตรวจแล้ว จาก '+s.rows+' แถว, ยังไม่ตรวจ '+s.blank+(s.auto_skipped?', ไม่นับ auto '+s.auto_skipped:'')+')</h2>';
 h+='<div class=muted>'+(s.from||'')+' ถึง '+(s.to||'')+' · NG = positive</div>';
 h+='<table><tr><th></th><th>คนว่า OK</th><th>คนว่า NG</th></tr><tr><td>ระบบบอก OK</td><td>TN '+s.tn+'</td><td>FN '+s.fn+'</td></tr><tr><td>ระบบบอก NG</td><td>FP '+s.fp+'</td><td>TP '+s.tp+'</td></tr></table><br>';
 if(s.weighted){h+='<div class=kpi><div>Accuracy ประมาณทั้งช่วง<div class=big>'+pc(s.weighted.accuracy)+'</div></div><div>Recall ประมาณ<div class=big>'+pc(s.weighted.recall)+'</div></div></div><br>'}
 h+='<table><tr><td>Accuracy (ในตัวอย่าง) = (TP+TN)/ทั้งหมด</td><td>'+pc(s.accuracy_sample)+'</td></tr><tr><td>Precision = TP/(TP+FP)</td><td>'+pc(s.precision)+'</td></tr>'
 +'<tr><td>Recall = TP/(TP+FN)</td><td>'+pc(s.recall_sample)+'</td></tr><tr><td>F1</td><td>'+pc(s.f1_sample)+'</td></tr>'
 +'<tr><td>False reject = FP/(TP+FP)</td><td>'+pc(s.false_reject)+'</td></tr><tr><td>Missed defect = FN/(TN+FN)</td><td>'+pc(s.missed_defect)+'</td></tr>'
 +(s.weighted?'<tr><td>F1 ประมาณทั้งช่วง</td><td>'+pc(s.weighted.f1)+'</td></tr>':'')+'</table>';
 if(!s.weighted)h+='<div class=muted>ตัวอย่างมี sensor NG ครึ่งหนึ่ง Accuracy ในตัวอย่างจึงไม่ใช่ของทั้งไลน์ ใส่จำนวน OK/NG จริงด้านบนเพื่อประมาณทั้งช่วง</div>';
 if(s.fr_tools.length)h+='<p>FP ตาม tool ที่ตัด: '+s.fr_tools.map(x=>x[0]+' x'+x[1]).join(', ')+'</p>';
 if(s.vs_system)h+='<p>คนตอบต่างจาก system_truth '+s.vs_system+' แถว</p>';
 if(s.few)h+='<p class=FN>มีชิ้นที่ตรวจน้อยกว่า 30 ในฝั่งใดฝั่งหนึ่ง ช่วงความเชื่อมั่นกว้าง</p>';
 const tb=(t,a)=>'<h2>'+t+'</h2><table><tr><th></th><th>n</th><th>TP</th><th>TN</th><th>FP</th><th>FN</th><th>Acc</th></tr>'+a.map(d=>'<tr><td>'+esc(d.name)+'</td><td>'+d.n+'</td><td>'+d.tp+'</td><td>'+d.tn+'</td><td>'+d.fp+'</td><td>'+d.fn+'</td><td>'+pc(d.accuracy)+'</td></tr>').join('')+'</table>';
 if(s.by_day.length>1)h+=tb('แยกรายวัน',s.by_day); if(s.by_file.length>1)h+=tb('แยกตามไฟล์',s.by_file);
 $('stats').innerHTML=h;}
function exp(){location='/api/export?'+qs()}
async function openf(){const f=$('lf').value;L={file:f,rows:[],i:0};if(!f){draw();return}
 let r=await (await fetch('/api/rows?file='+encodeURIComponent(f))).json(); if($('todo').checked)r=r.filter(x=>!x.truth);L.rows=r;draw()}
function draw(){const b=$('lbl');if(!L.file){b.innerHTML='<span class=muted>สร้างหรือเปิดไฟล์ label ก่อน</span>';return}
 if(!L.rows.length){b.innerHTML='<b>ตรวจครบแล้ว</b> <span class=muted>(เอาติ๊ก "เฉพาะที่ยังไม่ตรวจ" ออกเพื่อดู/แก้ทั้งหมด)</span>';return}
 L.i=Math.max(0,Math.min(L.i,L.rows.length-1));const r=L.rows[L.i];
 b.innerHTML=(r.img?'<img id=limg src="/img?p='+encodeURIComponent(r.img)+'">':'<div style="padding:40px;background:#eee">ไม่พบรูปในโฟลเดอร์</div>')
 +'<div style="margin:8px 0">'+(L.i+1)+' / '+L.rows.length+' · '+esc(r.image_file)+' · <span class=muted>'+esc(r.timestamp)+'</span></div>'
 +($('showsys').checked?'<div>ระบบ: <b>'+esc(r.sensor_result)+'</b> score '+esc(r.score)+' · '+esc(r.tools)+'</div>':'')
 +'<div style="margin:8px 0">คนตอบ: <b>'+(esc(r.truth)||'-')+'</b></div>'
 +'<button class=ok onclick="ans(\'OK\')">OK (1)</button> <button class=ng onclick="ans(\'NG\')">NG (2)</button> '
 +'<button class=sec onclick="ans(\'\')">ล้าง (0)</button> <button class=sec onclick="mv(-1)">← ก่อนหน้า</button> <button class=sec onclick="mv(1)">ถัดไป →</button> <span id=lmsg class=err></span>'}
function mv(d){L.i+=d;draw()}
async function ans(t){const r=L.rows[L.i];if(!r)return;
 const j=await (await fetch('/api/label?file='+encodeURIComponent(L.file)+'&uid='+encodeURIComponent(r.uid)+'&truth='+t,{method:'POST'})).json();
 r.truth=t; if(!j.ok){draw();$('lmsg').textContent=j.error;load();return}
 if(t&&L.i<L.rows.length-1)L.i++; draw(); load()}
document.addEventListener('keydown',e=>{if($('p1').style.display=='none'||['INPUT','SELECT'].includes(e.target.tagName))return;
 if(e.key=='1'||e.key=='o')ans('OK');else if(e.key=='2'||e.key=='n')ans('NG');else if(e.key=='0')ans('');
 else if(e.key=='ArrowRight')mv(1);else if(e.key=='ArrowLeft')mv(-1)});
function tile(img,lines){const t=document.createElement('div');t.className='tile';
 t.innerHTML=(img?'<img loading=lazy src="/img?p='+encodeURIComponent(img)+'">':'<div style="height:160px;background:#eee;display:flex;align-items:center;justify-content:center">ไม่พบรูปในโฟลเดอร์</div>')+'<div>'+lines+'</div>';
 if(img)t.onclick=()=>{$('vimg').src='/img?p='+encodeURIComponent(img);$('vtxt').innerHTML=lines;$('view').style.display='flex'};return t}
function rowTile(r){return tile(r.img,'<span class="'+r.kind+'">'+r.kind+'</span> ระบบ '+esc(r.sensor_result)+' / คน '+esc(r.truth||'-')+' / score '+esc(r.score)+(r.failing_tools?' / '+esc(r.failing_tools):'')+'<br>'+esc(r.image_file)+'<br><span class=muted>'+esc(r.timestamp)+' · '+esc(r.file)+'</span>')}
function show(list,extra){const res=$('res');res.innerHTML='';list.forEach(r=>res.appendChild(rowTile(r)));
 (extra||[]).forEach(p=>res.appendChild(tile(p,esc(p.split('/').pop())+'<br><span class=muted>'+esc(p)+'</span>')));
 if(!res.children.length)res.innerHTML='<div class=muted>ไม่พบ</div>'}
async function search(){const q=$('q').value.trim();if(!q)return;$('res').innerHTML='<div class=muted>กำลังค้นหา...</div>';
 const j=await (await fetch('/api/search?q='+encodeURIComponent(q))).json();show(j.rows,j.files)}
async function rows(k){$('res').innerHTML='<div class=muted>กำลังโหลด...</div>';show(await (await fetch('/api/rows?kind='+k)).json())}
const u0=new URLSearchParams(location.search);$('pok').value=u0.get('pop_ok')||'';$('png').value=u0.get('pop_ng')||'';
$('q').addEventListener('keydown',e=>{if(e.key=='Enter')search()});
load().then(openf);
</script></body></html>"""


# ------------------------------------------------------------------
# command line
# ------------------------------------------------------------------

def _default_root() -> Path | None:
    for cand in (os.environ.get("IV4_LABELTOOL_IMAGES"), r"D:\image-data-keyence"):
        if cand and Path(cand).is_dir():
            return Path(cand)
    return None


def run(args: list[str], settings=None) -> int:
    opt: dict = {"files": [], "images": None, "port": 8765, "browser": True, "text": False,
                 "pop_ok": None, "pop_ng": None, "person_only": False, "out": None}
    it = iter(args)
    try:
        for a in it:
            if a == "--images":
                opt["images"] = Path(next(it))
            elif a == "--port":
                opt["port"] = int(next(it))
            elif a == "--no-browser":
                opt["browser"] = False
            elif a == "--text":
                opt["text"] = True
            elif a == "--pop-ok":
                opt["pop_ok"] = int(next(it))
            elif a == "--pop-ng":
                opt["pop_ng"] = int(next(it))
            elif a == "--out":
                opt["out"] = Path(next(it))
            elif a == "--person-only":
                opt["person_only"] = True
            elif a in {"-h", "--help"}:
                print(__doc__)
                return 0
            elif a.startswith("-"):
                print(f"unknown option {a}\n{__doc__}")
                return 2
            else:
                opt["files"].append(Path(a))
    except (StopIteration, ValueError):
        print(f"Bad option value.\n{__doc__}")
        return 2
    root = opt["images"] or _default_root()
    if root is None and settings is not None and Path(str(getattr(settings, "archive_dir", ""))).is_dir():
        root = Path(settings.archive_dir)
    if root is not None and not root.is_dir():
        print(f"Image folder not found: {root}")
        return 2
    out = opt["out"] or (Path("reports") if settings is not None else Path(__file__).resolve().parent / "labels")
    state = State(Images(root), out)
    try:
        for f in opt["files"]:
            if not f.is_file():
                print(f"File not found: {f}")
                return 2
            n = state.add(f.name, f.read_bytes(), path=f)
            print(f"loaded {f.name}: {n} rows")
    except ImportError:
        print("openpyxl is missing: pip install openpyxl  (or save the file as CSV)")
        return 2
    if opt["text"]:
        if not opt["files"]:
            print("--text needs at least one file")
            return 2
        print(text_report(compute(state.rows(), opt["pop_ok"], opt["pop_ng"], opt["person_only"])))
        return 0
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", opt["port"]), make_handler(state))
    except OSError as exc:
        print(f"Cannot open port {opt['port']} ({exc}). Try --port 8766")
        return 2
    url = f"http://127.0.0.1:{opt['port']}/"
    if opt["pop_ok"] is not None and opt["pop_ng"] is not None:
        url += f"?pop_ok={opt['pop_ok']}&pop_ng={opt['pop_ng']}"
    print(f"Label tool: {url}   images: {root or '(none - use --images)'}   new label files: {out.resolve()}")
    print("Ctrl+C to stop")
    if opt["browser"]:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
