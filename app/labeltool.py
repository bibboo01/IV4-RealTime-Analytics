"""Label tool: accuracy from filled label_sample files + find and show images by file name, in the browser.

    run labeltool                                   open http://127.0.0.1:8765 (choose the Excel files there)
    run labeltool reports\\label_sample_*.xlsx      load these files at the start
    run labeltool --images D:\\image-data-keyence   the image folder (default: D:\\image-data-keyence, else the archive)
    run labeltool file.xlsx --text                  print the numbers only, no browser
    options: --port 8765  --no-browser  --pop-ok N --pop-ng N (real OK/NG totals of the period -> estimated accuracy)
             --person-only (leave out rows whose filled_by starts with 'auto')

Same numbers as `run sensor --labels`, but straight from the Excel files (no database needed): NG is the positive
class, Precision = of what the sensor rejected how much was really NG, Recall = of the real NG how much it caught.
The sample has as many sensor-OK as sensor-NG pieces, so the plain accuracy of the sample is not the line's
accuracy; give --pop-ok/--pop-ng (or type them on the page) to re-weight it like `run sensor` does.

The image folder is laid out like the archive: <date>\\<OK|NG>\\<hour>\\<uid>\\<image>.jpeg. A search for a name like
00351_13102026_051030 looks straight in 2026-10-13\\*\\05\\ (the date and hour are in the name); other text is
matched against the loaded rows, then the folder is walked (stops after --limit results).

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
        rows = [{h: ("" if v is None else str(v)) for h, v in zip(head, r) if h}
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
# web page
# ------------------------------------------------------------------

class State:
    def __init__(self, images: Images):
        self.images = images
        self.files: dict[str, list[dict]] = {}
        self.lock = threading.Lock()

    def rows(self) -> list[dict]:
        with self.lock:
            return [r for rs in self.files.values() for r in rs]

    def add(self, name: str, data: bytes) -> int:
        rows = read_rows(data, name)
        with self.lock:
            self.files[name] = rows
        return len(rows)


def _int(v) -> int | None:
    try:
        return int(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _row_out(r: dict, images: Images) -> dict:
    keys = ("uid", "timestamp", "sensor", "sensor_result", "truth", "system_truth", "filled_by", "score",
            "failing_tools", "tool1_status", "tool1_value", "tool2_status", "tool2_value", "image_file", "folder")
    d = {k: r.get(k, "") for k in keys if k in r}
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
                return self._json({"files": {k: len(v) for k, v in state.files.items()},
                                   "root": str(state.images.root or ""), "stats": st, "text": text_report(st)})
            if u.path == "/api/rows":
                want = q.get("kind", ["FP"])[0]
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
            if u.path == "/api/load":
                name = Path(q.get("name", ["file.xlsx"])[0]).name
                data = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                try:
                    n = state.add(name, data)
                except Exception as exc:  # noqa: BLE001 - shown on the page
                    return self._json({"error": f"{name}: {exc}"}, 400)
                return self._json({"ok": True, "rows": n})
            if u.path == "/api/remove":
                with state.lock:
                    state.files.pop(q.get("name", [""])[0], None)
                return self._json({"ok": True})
            return self._send(b"not found", "text/plain", 404)

    return H


PAGE = r"""<!DOCTYPE html><html lang="th"><head><meta charset="utf-8"><title>IV4 Label Accuracy</title>
<style>
body{font-family:Segoe UI,Tahoma,sans-serif;margin:0;background:#f5f6f8;color:#222}
header{background:#1f3a5f;color:#fff;padding:10px 18px;font-size:18px}
main{display:grid;grid-template-columns:420px 1fr;gap:14px;padding:14px}
.card{background:#fff;border-radius:8px;padding:12px 14px;box-shadow:0 1px 3px #0002;margin-bottom:14px}
h2{font-size:15px;margin:0 0 8px}
table{border-collapse:collapse;font-size:13px;width:100%}td,th{border:1px solid #ddd;padding:4px 6px;text-align:right}
th{background:#eef1f5}td:first-child,th:first-child{text-align:left}
.big{font-size:26px;font-weight:600}.muted{color:#777;font-size:12px}
input[type=text],input[type=number]{padding:6px;border:1px solid #bbb;border-radius:4px}
button{padding:6px 12px;border:0;border-radius:4px;background:#1f3a5f;color:#fff;cursor:pointer}
button.sec{background:#e3e7ee;color:#222}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:10px}
.tile{background:#fff;border:1px solid #ddd;border-radius:6px;overflow:hidden;cursor:pointer;font-size:12px}
.tile img{width:100%;height:160px;object-fit:contain;background:#111}.tile div{padding:5px}
.FP{color:#c0392b;font-weight:600}.FN{color:#d35400;font-weight:600}.TP,.TN{color:#27ae60}
#view{position:fixed;inset:0;background:#000d;display:none;align-items:center;justify-content:center;z-index:9;flex-direction:column}
#view img{max-width:95vw;max-height:85vh}#view div{color:#fff;margin-top:8px;font-size:14px}
.kpi{display:grid;grid-template-columns:1fr 1fr;gap:8px}.kpi>div{background:#f7f9fc;border-radius:6px;padding:8px}
</style></head><body>
<header>IV4 Label Accuracy - คำนวณสถิติจาก label_sample และค้นหารูป</header>
<main><section>
<div class="card"><h2>1. ไฟล์ Excel (label_sample)</h2>
<input type="file" id="f" multiple accept=".xlsx,.csv"> <div id="files" class="muted"></div></div>
<div class="card"><h2>2. ตัวเลือก</h2>
<div>จำนวนจริงทั้งช่วงเวลา (ถ้ามี) <br>OK <input type="number" id="pok" style="width:110px"> NG <input type="number" id="png" style="width:90px"></div>
<label><input type="checkbox" id="po"> ไม่นับแถวที่ระบบเติมเอง (filled_by = auto...)</label>
<div style="margin-top:6px"><button onclick="load()">คำนวณใหม่</button> <button class="sec" onclick="exp()">ดาวน์โหลด Excel สรุป</button></div></div>
<div class="card" id="stats"></div>
</section><section>
<div class="card"><h2>ค้นหารูปจากชื่อไฟล์</h2>
<input type="text" id="q" style="width:60%" placeholder="เช่น 00351_13102026_051030 หรือ uid"> <button onclick="search()">ค้นหา</button>
<span class="muted">โฟลเดอร์รูป: <span id="root"></span></span>
<div style="margin-top:8px">แสดงแถว: <button class="sec" onclick="rows('FP')">Sensor NG แต่จริง OK (FP)</button>
<button class="sec" onclick="rows('FN')">Sensor OK แต่จริง NG (FN)</button> <button class="sec" onclick="rows('all')">ทั้งหมด</button></div></div>
<div id="res" class="grid"></div></section></main>
<div id="view" onclick="this.style.display='none'"><img id="vimg"><div id="vtxt"></div></div>
<script>
const $=id=>document.getElementById(id);
const pc=p=>p==null?'-':(typeof p=='number'?p.toFixed(2)+'%':(p.pct==null?'-':p.pct.toFixed(1)+'% <span class=muted>('+p.lo.toFixed(1)+'-'+p.hi.toFixed(1)+'%) '+p.k+'/'+p.n+'</span>'));
function qs(){return 'pop_ok='+($('pok').value||'')+'&pop_ng='+($('png').value||'')+'&person_only='+($('po').checked?1:0)}
$('f').onchange=async e=>{for(const f of e.target.files){const r=await fetch('/api/load?name='+encodeURIComponent(f.name),{method:'POST',body:await f.arrayBuffer()});
 const j=await r.json(); if(j.error) alert(j.error);} e.target.value=''; load();};
async function rm(n){await fetch('/api/remove?name='+encodeURIComponent(n),{method:'POST'});load()}
async function load(){const j=await (await fetch('/api/state?'+qs())).json(); $('root').textContent=j.root||'(ไม่ได้ตั้ง)';
 $('files').innerHTML=Object.entries(j.files).map(([k,v])=>k+' ('+v+' แถว) <a href="#" onclick="rm(\''+k.replace(/'/g,"\\'")+'\');return false">ลบ</a>').join('<br>')||'ยังไม่ได้เลือกไฟล์';
 const s=j.stats; if(!s.checked){$('stats').innerHTML='<h2>ผล</h2>ยังไม่มีแถวที่กรอก truth (OK/NG)';return}
 let h='<h2>ผล ('+s.checked+' ชิ้นที่ตรวจแล้ว จาก '+s.rows+' แถว, ว่าง '+s.blank+(s.auto_skipped?', ไม่นับ auto '+s.auto_skipped:'')+')</h2>';
 h+='<div class=muted>'+(s.from||'')+' ถึง '+(s.to||'')+'</div>';
 h+='<table><tr><th></th><th>จริง OK</th><th>จริง NG</th></tr><tr><td>Sensor บอก OK</td><td>'+s.tn+' (TN)</td><td>'+s.fn+' (FN)</td></tr><tr><td>Sensor บอก NG</td><td>'+s.fp+' (FP)</td><td>'+s.tp+' (TP)</td></tr></table><br>';
 if(s.weighted){h+='<div class=kpi><div>Accuracy ประมาณทั้งช่วง<div class=big>'+pc(s.weighted.accuracy)+'</div></div><div>Recall ประมาณ<div class=big>'+pc(s.weighted.recall)+'</div></div></div><br>'}
 h+='<table><tr><td>Accuracy (ในตัวอย่าง)</td><td>'+pc(s.accuracy_sample)+'</td></tr><tr><td>False reject (NG แต่จริง OK)</td><td>'+pc(s.false_reject)+'</td></tr>'
 +'<tr><td>Missed defect (OK แต่จริง NG)</td><td>'+pc(s.missed_defect)+'</td></tr><tr><td>Precision</td><td>'+pc(s.precision)+'</td></tr>'
 +'<tr><td>Recall (ในตัวอย่าง)</td><td>'+pc(s.recall_sample)+'</td></tr><tr><td>F1 (ในตัวอย่าง)</td><td>'+pc(s.f1_sample)+'</td></tr>'
 +(s.weighted?'<tr><td>F1 ประมาณทั้งช่วง</td><td>'+pc(s.weighted.f1)+'</td></tr>':'')+'</table>';
 if(!s.weighted)h+='<div class=muted>ตัวอย่างมี sensor NG ครึ่งหนึ่ง Accuracy ในตัวอย่างจึงไม่ใช่ของทั้งไลน์ ใส่จำนวน OK/NG จริงด้านบนเพื่อประมาณทั้งช่วง</div>';
 if(s.fr_tools.length)h+='<p>False reject ตาม tool: '+s.fr_tools.map(x=>x[0]+' x'+x[1]).join(', ')+'</p>';
 if(s.vs_system)h+='<p>truth ต่างจาก system_truth '+s.vs_system+' แถว</p>';
 if(s.few)h+='<p class=FN>มีชิ้นที่ตรวจน้อยกว่า 30 ในฝั่งใดฝั่งหนึ่ง ช่วงความเชื่อมั่นกว้าง</p>';
 if(s.by_day.length>1){h+='<h2>แยกรายวัน</h2><table><tr><th>วัน</th><th>n</th><th>Acc</th><th>FR</th><th>Missed</th></tr>'+s.by_day.map(d=>'<tr><td>'+d.name+'</td><td>'+d.n+'</td><td>'+pc(d.accuracy)+'</td><td>'+pc(d.false_reject)+'</td><td>'+pc(d.missed_defect)+'</td></tr>').join('')+'</table>'}
 if(s.by_file.length>1){h+='<h2>แยกตามไฟล์</h2><table><tr><th>ไฟล์</th><th>n</th><th>Acc</th><th>FR</th><th>Missed</th></tr>'+s.by_file.map(d=>'<tr><td>'+d.name+'</td><td>'+d.n+'</td><td>'+pc(d.accuracy)+'</td><td>'+pc(d.false_reject)+'</td><td>'+pc(d.missed_defect)+'</td></tr>').join('')+'</table>'}
 $('stats').innerHTML=h;}
function exp(){location='/api/export?'+qs()}
function esc(s){return String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function tile(img,lines){const t=document.createElement('div');t.className='tile';
 t.innerHTML=(img?'<img loading=lazy src="/img?p='+encodeURIComponent(img)+'">':'<div style="height:160px;background:#eee;display:flex;align-items:center;justify-content:center">ไม่พบรูปในโฟลเดอร์</div>')+'<div>'+lines+'</div>';
 if(img)t.onclick=()=>{$('vimg').src='/img?p='+encodeURIComponent(img);$('vtxt').innerHTML=lines;$('view').style.display='flex'};return t}
function rowTile(r){return tile(r.img,'<span class="'+r.kind+'">'+r.kind+'</span> sensor '+esc(r.sensor_result)+' / truth '+esc(r.truth||'-')+' / score '+esc(r.score)+(r.failing_tools?' / '+esc(r.failing_tools):'')+'<br>'+esc(r.image_file)+'<br><span class=muted>'+esc(r.timestamp)+' · '+esc(r.file)+'</span>')}
async function show(list,extra){const res=$('res');res.innerHTML='';list.forEach(r=>res.appendChild(rowTile(r)));
 (extra||[]).forEach(p=>res.appendChild(tile(p,esc(p.split('/').pop())+'<br><span class=muted>'+esc(p)+'</span>')));
 if(!res.children.length)res.innerHTML='<div class=muted>ไม่พบ</div>'}
async function search(){const q=$('q').value.trim();if(!q)return;$('res').innerHTML='<div class=muted>กำลังค้นหา...</div>';
 const j=await (await fetch('/api/search?q='+encodeURIComponent(q))).json();show(j.rows,j.files)}
async function rows(k){$('res').innerHTML='<div class=muted>กำลังโหลด...</div>';show(await (await fetch('/api/rows?kind='+k)).json())}
const u0=new URLSearchParams(location.search);$('pok').value=u0.get('pop_ok')||'';$('png').value=u0.get('pop_ng')||'';
$('q').addEventListener('keydown',e=>{if(e.key=='Enter')search()});
load();
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
                 "pop_ok": None, "pop_ng": None, "person_only": False}
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
    state = State(Images(root))
    try:
        for f in opt["files"]:
            if not f.is_file():
                print(f"File not found: {f}")
                return 2
            n = state.add(f.name, f.read_bytes())
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
    print(f"Label tool: {url}   images: {root or '(none - use --images)'}   Ctrl+C to stop")
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
