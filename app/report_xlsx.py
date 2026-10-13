"""
Excel report with real charts and a written summary: `run report --excel` (see app/report.py for the options).

Everything comes from the hourly tables (fast, never scans the image folders), except the optional NG sample
pictures, which read a handful of files. The summary is written by rules from the numbers, so it always matches
them; what it cannot know (lot, lighting, who changed what) is left to the people on the line.
"""
from __future__ import annotations

import re
import statistics
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import text

from app.metrics import summarize, tool_summary

LOW_VOLUME = 0.90          # an hour below 90% of the typical hour = the line probably stopped for a while
HIGH_NG_MIN = 1.0          # % - an hour is "high NG" at >= max(this, 2x the period's median) ...
HIGH_NG_MIN_TOTAL = 200    # ... with at least this many inspections
HOURLY_COLUMN_LIMIT = 72   # above this many hours the hourly charts become lines

TXT = {
    "th": {
        "title": "รายงานสรุป IV4 Data Agent", "range": "ช่วงข้อมูล {a} ถึง {b} ({n} ชั่วโมงที่มีข้อมูล)",
        "kpi": ["จำนวนตรวจทั้งหมด", "NG รวม", "NG %", "Yield", "ไฟล์หายรวม", "เวลาตรวจเฉลี่ย (ms)"],
        "summary": "สรุปละเอียด",
        "h_overview": "ภาพรวม", "h_flow": "ปริมาณงาน", "h_low": "ยอดต่ำกว่าปกติ", "h_ng": "คุณภาพ (NG)",
        "h_high": "ช่วงที่ NG สูง", "h_pattern": "ซ้ำเวลาเดิม", "h_tool": "Tool ที่ทำให้ NG", "h_drift": "Drift ตอนนี้",
        "h_notes": "ข้อสังเกต", "h_next": "สิ่งที่ควรตรวจต่อ",
        "overview": "ตรวจทั้งหมด {total:,} ชุด ใน {hours} ชั่วโมง ไฟล์หาย {missing} ({missing_pct}) NG {ng:,} ชุด ({ng_pct:.2f}%) yield {yield_pct:.2f}%",
        "missing_none": "ไม่มีการวัดไฟล์หายได้", "missing_fmt": "{m:,} ชุด",
        "flow": "ชั่วโมงเต็มโดยทั่วไปได้ ~{typ:,.0f} ชุด (≈ {rate:.2f} ชุด/วินาที) เวลาตรวจเฉลี่ย {ms:.1f} ms",
        "flow_noms": "ชั่วโมงเต็มโดยทั่วไปได้ ~{typ:,.0f} ชุด (≈ {rate:.2f} ชุด/วินาที)",
        "low_none": "ไม่พบชั่วโมงที่ยอดต่ำผิดปกติ",
        "low": "{n} ชั่วโมงที่ยอดต่ำกว่าปกติ: {items} (น่าจะสายหยุด/ไม่มี trigger ถ้าคอลัมน์ไฟล์หายเป็น 0 แปลว่าไม่ใช่ข้อมูลหาย)",
        "low_item": "{h} ได้ {t:,} (ขาด ≈ {m:.0f} นาที)",
        "ng": "NG รวม {ng_pct:.2f}% ค่ากลางรายชั่วโมง {med:.2f}% สูงสุด {mx:.2f}% ที่ {mxh}",
        "high_none": "ไม่พบชั่วโมงที่ NG สูงผิดปกติ",
        "high": "{n} ชั่วโมงที่ NG สูง (≥ {thr:.1f}%): {items} รวมกัน {share:.0f}% ของ NG ทั้งหมด ควรเทียบกับบันทึกหน้างาน เช่น เปลี่ยนล็อต ปรับแสง/เลนส์ เริ่มเดินสาย",
        "high_item": "{h} {p:.2f}% ({n:,}/{t:,})",
        "pattern_none": "ยังไม่พบชั่วโมงของวันที่ NG สูงซ้ำกันหลายวัน",
        "pattern": "NG สูงซ้ำที่เวลา {hours} ของวัน (เกิดในหลายวัน) น่าสงสัยปัจจัยที่เปลี่ยนตามเวลา เช่น แสงจากภายนอก อุณหภูมิ กะ/ล็อต (เป็นข้อสันนิษฐานจากข้อมูล ยังต้องยืนยันหน้างาน)",
        "pattern_short": "ข้อมูลน้อยกว่า 2 วัน จึงยังดูรูปแบบตามเวลาของวันไม่ได้",
        "tool_none": "ไม่มีข้อมูลรายละเอียดต่อ tool",
        "tool": "{items}",
        "tool_item": "{n} NG {c:,} ครั้ง ({p:.2f}%) เกี่ยวข้องกับ {share:.0f}% ของ NG ทั้งหมด (ชิ้นหนึ่งอาจ NG หลาย tool จึงรวมกันเกิน 100% ได้)",
        "tool_one": "NG ส่วนใหญ่มาจาก {n} เพียงตัวเดียว ถ้า tool อื่นปกติ ปัญหามักอยู่ที่ชิ้นส่วน/กรอบตรวจของ tool นั้น ไม่ใช่เลนส์หรือแสงรวม",
        "drift_none": "ทุก tool ยังอยู่ในช่วงปกติ 7 วัน หรือยังไม่มีข้อมูลเทียบพอ",
        "drift": "เบี่ยงจากปกติ: {items}",
        "notes_partial": "ชั่วโมงแรกและชั่วโมงสุดท้ายของช่วงข้อมูลอาจไม่เต็มชั่วโมง ไม่นำมาตัดสินว่ายอดต่ำ",
        "notes_star": "เครื่องหมาย * ที่ไฟล์หาย = บางชั่วโมงวัดไม่ได้ (ตัวนับของเซ็นเซอร์รีเซ็ต)",
        "notes_cause": "รายงานนี้บอก 'อะไรผิดปกติ และเมื่อไร' ส่วนสาเหตุ (ล็อต แสง เลนส์ ค่าตั้ง) ต้องเทียบกับบันทึกหน้างาน",
        "next_tool": "เปิดรูป NG ล่าสุดของ {n} (แผ่น NG ตัวอย่าง) ดูว่า AI ตัดสินผิดตรงไหน และเช็กชิ้นส่วนที่ tool นั้นตรวจ",
        "next_high": "เทียบชั่วโมงที่ NG สูงกับบันทึกหน้างาน (เปลี่ยนล็อต ทำความสะอาด ปรับแสง)",
        "next_hw": "ฮาร์ดแวร์ที่ควรมี: UPS และ SSD เพื่อกันข้อมูลสะดุดช่วงไฟดับ/ดิสก์ช้า",
        "sheets": {"s": "สรุป", "g": "กราฟ", "h": "รายชั่วโมง", "d": "รายวัน", "t": "Tool ต่อชั่วโมง", "p": "ตามชั่วโมงของวัน",
                   "r": "NG ตามสาเหตุ", "x": "Drift", "i": "NG ตัวอย่าง"},
        "hourly_head": ["ชั่วโมง", "จำนวนตรวจ", "NG", "NG %", "Yield %", "เวลาตรวจเฉลี่ย (ms)", "ไฟล์หาย", "NG % รวมทั้งช่วง"],
        "daily_head": ["วันที่", "จำนวนตรวจ", "NG", "NG %", "Yield %", "ไฟล์หาย", "ชั่วโมงที่มีข้อมูล"],
        "tool_head": ["ชั่วโมง", "Sensor", "Tool", "ชื่อ", "จำนวน", "NG", "NG %", "score เฉลี่ย"],
        "tool_sum_head": ["Sensor", "Tool", "ชื่อ", "จำนวน", "NG", "NG %", "score เฉลี่ย", "ค่าต่ำสุดที่ยังผ่าน"],
        "prof_head": ["ชั่วโมงของวัน", "จำนวนตรวจ", "NG", "NG %", "จำนวนวันที่มีข้อมูล"],
        "reason_head": ["สาเหตุ NG", "จำนวน"], "drift_head": ["Tool", "score ล่าสุด", "score ปกติ", "NG % ล่าสุด", "NG % ปกติ", "สถานะ"],
        "sample_head": ["เวลา", "Sensor", "สาเหตุ"], "total": "รวม", "partial_note": "หมายเหตุ: ชั่วโมงแรก/สุดท้ายอาจไม่เต็มชั่วโมง",
        "charts": ["จำนวนตรวจต่อชั่วโมง", "NG % ต่อชั่วโมง เทียบค่ารวม", "จำนวน NG ต่อชั่วโมง", "NG % รายวัน",
                   "NG % ต่อชั่วโมง แยกตาม tool", "NG % ตามชั่วโมงของวัน (รวมทุกวัน)", "สาเหตุ NG สูงสุด"],
        "ok": "ปกติ", "drift_flag": "เบี่ยง", "cannot": "เทียบไม่ได้", "no_sample": "ไม่พบรูปที่ยังอยู่ใน archive",
    },
    "en": {
        "title": "IV4 Data Agent summary report", "range": "Data from {a} to {b} ({n} hours with data)",
        "kpi": ["Inspections", "NG", "NG %", "Yield", "Missing files", "Avg inspection time (ms)"],
        "summary": "Detailed summary",
        "h_overview": "Overview", "h_flow": "Throughput", "h_low": "Low-volume hours", "h_ng": "Quality (NG)",
        "h_high": "High-NG hours", "h_pattern": "Repeats at the same time", "h_tool": "Tools causing NG", "h_drift": "Drift now",
        "h_notes": "Notes", "h_next": "What to check next",
        "overview": "{total:,} inspections in {hours} hours; missing files {missing} ({missing_pct}); NG {ng:,} ({ng_pct:.2f}%); yield {yield_pct:.2f}%",
        "missing_none": "not measurable", "missing_fmt": "{m:,}",
        "flow": "A full hour is typically ~{typ:,.0f} inspections (≈ {rate:.2f}/s); average inspection time {ms:.1f} ms",
        "flow_noms": "A full hour is typically ~{typ:,.0f} inspections (≈ {rate:.2f}/s)",
        "low_none": "No hour with unusually low volume.",
        "low": "{n} hour(s) below the usual volume: {items} (probably the line stopped / no trigger; if Missing is 0 it is not lost data)",
        "low_item": "{h} had {t:,} (≈ {m:.0f} min short)",
        "ng": "Overall NG {ng_pct:.2f}%, median hour {med:.2f}%, worst {mx:.2f}% at {mxh}",
        "high_none": "No hour with unusually high NG.",
        "high": "{n} high-NG hour(s) (>= {thr:.1f}%): {items}; together {share:.0f}% of all NG. Compare with the line log (lot change, light/lens, start-up).",
        "high_item": "{h} {p:.2f}% ({n:,}/{t:,})",
        "pattern_none": "No clock hour with high NG on several days.",
        "pattern": "NG is high at {hours} on several days: suspect something that follows the time of day (outside light, temperature, shift/lot). A hypothesis from the data - confirm on the line.",
        "pattern_short": "Less than 2 days of data: time-of-day patterns cannot be judged yet.",
        "tool_none": "No per-tool detail.",
        "tool": "{items}",
        "tool_item": "{n} NG {c:,} ({p:.2f}%), involved in {share:.0f}% of all NG (one piece can fail several tools, so shares can add up to more than 100%)",
        "tool_one": "Most NG comes from {n} alone. If the other tools are normal, the cause is usually the part / region that tool inspects, not the lens or overall light.",
        "drift_none": "All tools are within their 7-day normal, or there is not enough history to compare.",
        "drift": "Away from normal: {items}",
        "notes_partial": "The first and last hour of the period may be partial; they are not judged as low volume.",
        "notes_star": "'*' on missing files = some hours could not be measured (sensor counter reset).",
        "notes_cause": "This report says what is abnormal and when. The cause (lot, light, lens, settings) must be matched with the line log.",
        "next_tool": "Open the latest NG pictures of {n} (sheet NG samples): see where the AI judged it wrong and check the part that tool inspects.",
        "next_high": "Match the high-NG hours with the line log (lot change, cleaning, light adjustment).",
        "next_hw": "Hardware worth having: UPS and an SSD, so power cuts / a slow disk do not interrupt collection.",
        "sheets": {"s": "Summary", "g": "Charts", "h": "Hourly", "d": "Daily", "t": "Tool by hour", "p": "By hour of day",
                   "r": "NG reasons", "x": "Drift", "i": "NG samples"},
        "hourly_head": ["Hour", "Inspections", "NG", "NG %", "Yield %", "Avg time (ms)", "Missing", "Period NG %"],
        "daily_head": ["Date", "Inspections", "NG", "NG %", "Yield %", "Missing", "Hours with data"],
        "tool_head": ["Hour", "Sensor", "Tool", "Name", "Count", "NG", "NG %", "Avg score"],
        "tool_sum_head": ["Sensor", "Tool", "Name", "Count", "NG", "NG %", "Avg score", "Lowest OK score"],
        "prof_head": ["Hour of day", "Inspections", "NG", "NG %", "Days with data"],
        "reason_head": ["NG reason", "Count"], "drift_head": ["Tool", "Recent score", "Normal score", "Recent NG %", "Normal NG %", "State"],
        "sample_head": ["Time", "Sensor", "Reason"], "total": "Total", "partial_note": "Note: the first/last hour may be partial.",
        "charts": ["Inspections per hour", "NG % per hour vs period average", "NG count per hour", "NG % per day",
                   "NG % per hour by tool", "NG % by hour of day (all days)", "Top NG reasons"],
        "ok": "ok", "drift_flag": "DRIFT", "cannot": "cannot compare", "no_sample": "No picture is still in the archive",
    },
}


@dataclass
class Data:
    start: str
    end: str
    hours: list = field(default_factory=list)
    days: list = field(default_factory=list)
    tool_hours: list = field(default_factory=list)      # (hour, sensor, tool, name, count, ng, vsum, vcount)
    tools: list = field(default_factory=list)
    reasons: list = field(default_factory=list)         # (text, count)
    drift: list = field(default_factory=list)
    samples: list = field(default_factory=list)         # (timestamp, sensor, reason, Path)


def _norm_reason(r: str | None) -> str:
    r = re.sub(r"\(value=[^)]*\)", "", r or "").strip(" ;")
    return re.sub(r"\s+", " ", r) or "(no reason)"


def collect(repo, s, start: str, end: str, now: datetime | None = None, images: int = 0) -> Data:
    """start/end 'YYYY-MM-DD', end exclusive."""
    from app.drift import compare
    now = now or datetime.now()
    d = Data(start, end)
    d.hours = [r for r in summarize(repo, start, end, by="hour") if r.total]
    d.days = summarize(repo, start, end, by="day")
    d.tools = sorted(tool_summary(repo, start, end, per_sensor=True), key=lambda x: -(x.ng_count or 0))
    with repo.engine.connect() as c:
        d.tool_hours = [tuple(r) for r in c.execute(text(
            "SELECT hour, sensor_id, tool_no, MAX(tool_name), SUM(count), SUM(ng_count), SUM(value_sum), SUM(value_count)"
            " FROM hourly_tool_stats WHERE hour >= :a AND hour < :b GROUP BY hour, sensor_id, tool_no"
            " ORDER BY hour, sensor_id, tool_no"), {"a": start, "b": end})]
        merged: dict[str, int] = {}
        for reason, n in c.execute(text(
                "SELECT analysis_reason, COUNT(*) FROM inspection WHERE analysis_status = 'FAIL'"
                " AND timestamp >= :a AND timestamp < :b GROUP BY analysis_reason"), {"a": start, "b": end}):
            key = _norm_reason(reason)
            merged[key] = merged.get(key, 0) + n
        d.reasons = sorted(merged.items(), key=lambda kv: -kv[1])[:15]
        if images > 0:
            for ts, sensor, reason, folder, image in c.execute(text(
                    "SELECT timestamp, camera_id, analysis_reason, folder, image_file FROM inspection"
                    " WHERE analysis_status = 'FAIL' AND timestamp >= :a AND timestamp < :b AND folder IS NOT NULL"
                    " AND image_file IS NOT NULL ORDER BY id DESC LIMIT :n"), {"a": start, "b": end, "n": images * 4}):
                p = s.archive_dir / folder / image
                if p.exists():
                    d.samples.append((ts, sensor, reason, p))
                if len(d.samples) >= images:
                    break
    d.drift = compare(repo, now, s.drift_min)
    return d


def _median(xs):
    return statistics.median(xs) if xs else 0.0


def findings(d: Data, s, lang: str = "th") -> list[tuple[str, str]]:
    """[(heading, text)] written from the numbers."""
    t = TXT[lang]
    out: list[tuple[str, str]] = []
    hours = d.hours
    total = sum(h.total for h in hours)
    if not total:
        return out
    ng = sum(h.fail_count for h in hours)
    ok = sum(h.pass_count for h in hours)
    ng_pct = ng / total * 100
    miss_vals = [h.missing for h in hours if h.missing is not None]
    missing = sum(miss_vals) if miss_vals else None
    expected = sum(h.expected or 0 for h in hours if h.expected)
    miss_txt = t["missing_none"] if missing is None else t["missing_fmt"].format(m=missing)
    miss_pct = "-" if missing is None or not expected else f"{missing / (expected or 1) * 100:.3f}%"
    out.append((t["h_overview"], t["overview"].format(total=total, hours=len(hours), missing=miss_txt, missing_pct=miss_pct,
                                                      ng=ng, ng_pct=ng_pct, yield_pct=ok / total * 100)))
    # flow
    inner = hours[1:-1] if len(hours) > 2 else hours
    typ = _median([h.total for h in inner]) if inner else 0
    times = [h.avg_time_ms for h in hours if h.avg_time_ms]
    if typ:
        key = "flow" if times else "flow_noms"
        out.append((t["h_flow"], t[key].format(typ=typ, rate=typ / 3600, ms=_median(times) if times else 0)))
    # low volume (first / last hour may be partial)
    low = [h for h in inner if typ and h.total < LOW_VOLUME * typ]
    if low:
        items = "; ".join(t["low_item"].format(h=_hl(h.period), t=h.total, m=(typ - h.total) / (typ / 60)) for h in low[:6])
        out.append((t["h_low"], t["low"].format(n=len(low), items=items)))
    else:
        out.append((t["h_low"], t["low_none"]))
    # NG
    pcts = [h.ng_pct or 0 for h in hours if h.total >= HIGH_NG_MIN_TOTAL]
    med = _median(pcts)
    worst = max(hours, key=lambda h: (h.ng_pct or 0) if h.total >= HIGH_NG_MIN_TOTAL else -1)
    out.append((t["h_ng"], t["ng"].format(ng_pct=ng_pct, med=med, mx=worst.ng_pct or 0, mxh=_hl(worst.period))))
    thr = max(HIGH_NG_MIN, 2 * med)
    high = sorted((h for h in hours if h.total >= HIGH_NG_MIN_TOTAL and (h.ng_pct or 0) >= thr), key=lambda h: -(h.ng_pct or 0))
    if high:
        items = "; ".join(t["high_item"].format(h=_hl(h.period), p=h.ng_pct, n=h.fail_count, t=h.total) for h in high[:6])
        share = sum(h.fail_count for h in high) / ng * 100 if ng else 0
        out.append((t["h_high"], t["high"].format(n=len(high), thr=thr, items=items, share=share)))
    else:
        out.append((t["h_high"], t["high_none"]))
    # same clock hour on several days
    if len({h.period[:10] for h in hours}) >= 2:
        by_clock: dict[str, set] = {}
        for h in high:
            by_clock.setdefault(h.period[-2:], set()).add(h.period[:10])
        rep = sorted(k for k, days in by_clock.items() if len(days) >= 2)
        out.append((t["h_pattern"], t["pattern"].format(hours=", ".join(f"{k}:00" for k in rep)) if rep else t["pattern_none"]))
    else:
        out.append((t["h_pattern"], t["pattern_short"]))
    # tools
    tools = [x for x in d.tools if x.count]
    tool_ng = sum(x.ng_count or 0 for x in tools)
    if tools:
        items = "; ".join(t["tool_item"].format(n=_tname(x), c=x.ng_count or 0, p=x.ng_pct or 0,
                                                share=(x.ng_count or 0) / ng * 100 if ng else 0) for x in tools[:4])
        text_ = t["tool"].format(items=items)
        top, rest = tools[0], tools[1:]
        if rest and ng and (top.ng_pct or 0) >= 2 * max((r.ng_pct or 0) for r in rest) and (top.ng_count or 0) / max(tool_ng, 1) > 0.6:
            text_ += " " + t["tool_one"].format(n=_tname(top))
        out.append((t["h_tool"], text_))
    else:
        out.append((t["h_tool"], t["tool_none"]))
    # drift now
    flagged = _drift_flags(d, s)
    out.append((t["h_drift"], t["drift"].format(items="; ".join(flagged)) if flagged else t["drift_none"]))
    # notes + next
    out.append((t["h_notes"], " ".join([t["notes_partial"], t["notes_star"] if any(h.unknown_hours for h in hours) else "",
                                        t["notes_cause"]]).replace("  ", " ")))
    nxt = []
    if tools and tool_ng and ng_pct >= max(HIGH_NG_MIN, 2 * 0.4):
        nxt.append(t["next_tool"].format(n=_tname(tools[0])))
    if high:
        nxt.append(t["next_high"])
    nxt.append(t["next_hw"])
    out.append((t["h_next"], "\n".join(f"{i}) {x}" for i, x in enumerate(nxt, 1))))
    return out


def _tname(x) -> str:
    return f"{x.sensor_id} tool {x.tool_no}" + (f" {x.tool_name}" if x.tool_name else "")


def _hl(h: str) -> str:
    return f"{h[5:10].replace('-', '/')} {h[-2:]}:00"


def _drift_flags(d: Data, s) -> list[str]:
    flags = []
    for r in d.drift:
        if r["skip"]:
            continue
        who = f"{r['sensor']} tool {r['tool']}"
        if r["base_avg"] and r["recent_avg"] is not None and abs(r["recent_avg"] - r["base_avg"]) / abs(r["base_avg"]) * 100 >= s.drift_pct > 0:
            flags.append(f"{who} score {r['recent_avg']:.1f} vs {r['base_avg']:.1f}")
        if r["base_ng"] is not None and r["recent_ng"] >= 2 * r["base_ng"] and r["recent_ng"] - r["base_ng"] >= 1 and s.drift_pct > 0:
            flags.append(f"{who} NG {r['recent_ng']:.2f}% vs {r['base_ng']:.2f}%")
    return flags


# ----------------------------------------------------------------------------------------------------------
# workbook
# ----------------------------------------------------------------------------------------------------------

def ensure_openpyxl() -> str | None:
    """None when openpyxl can be imported; otherwise try one pip install and return an error text on failure."""
    try:
        import openpyxl  # noqa: F401
        return None
    except ImportError:
        pass
    req = Path(__file__).resolve().parent.parent / "requirements-report.txt"
    what = ["-r", str(req)] if req.exists() else ["openpyxl==3.1.5"]
    print("Excel reports need the 'openpyxl' library; installing it once (needs internet)...")
    try:
        r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", *what], capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"could not run pip: {exc}"
    if r.returncode != 0:
        return (r.stderr or r.stdout).strip().splitlines()[-1][:200] if (r.stderr or r.stdout).strip() else "pip failed"
    try:
        import openpyxl  # noqa: F401,F811
        return None
    except ImportError as exc:
        return str(exc)


def write(d: Data, s, path: Path, lang: str = "th", now: datetime | None = None) -> Path:
    from openpyxl import Workbook
    from openpyxl.chart import BarChart, LineChart, Reference
    from openpyxl.chart.label import DataLabelList
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    t = TXT[lang]
    sh = t["sheets"]
    now = now or datetime.now()
    BLUE, RED, GREY, AMBER = "1F5FA8", "D64545", "F3F5F8", "FFF4D6"
    head_font, head_fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor=BLUE)
    line = Border(bottom=Side(style="thin", color="D5DAE1"))

    wb = Workbook()
    ws = wb.active
    ws.title = sh["h"]

    def header(sheet, row, labels):
        for i, lab in enumerate(labels, 1):
            c = sheet.cell(row, i, lab)
            c.font, c.fill = head_font, head_fill
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        sheet.row_dimensions[row].height = 32

    hours = d.hours
    total = sum(h.total for h in hours)
    ng = sum(h.fail_count for h in hours)
    avg_ng = ng / total if total else 0
    # --- hourly
    header(ws, 1, t["hourly_head"])
    for i, h in enumerate(hours, 2):
        ws.append([_hl(h.period), h.total, h.fail_count,
                   (h.ng_pct or 0) / 100, (h.yield_pct or 0) / 100, h.avg_time_ms, h.missing, avg_ng])
        for col in (4, 5, 8):
            ws.cell(i, col).number_format = "0.00%"
        ws.cell(i, 2).number_format = "#,##0"
    last_h = len(hours) + 1
    if hours:
        r = last_h + 2
        miss = [h.missing for h in hours if h.missing is not None]
        times = [h.avg_time_ms for h in hours if h.avg_time_ms]
        ws.append([])
        ws.append([t["total"], total, ng, avg_ng, 1 - avg_ng, sum(times) / len(times) if times else None, sum(miss) if miss else None])
        for c in ws[r]:
            c.font, c.fill = Font(bold=True), PatternFill("solid", fgColor=GREY)
        ws.cell(r, 2).number_format = "#,##0"
        for col in (4, 5):
            ws.cell(r, col).number_format = "0.00%"
        ws.cell(r, 6).number_format = "0.0"
        ws.cell(r + 2, 1, t["partial_note"]).font = Font(italic=True, color="666666")
    med = _median([(h.ng_pct or 0) for h in hours if h.total >= HIGH_NG_MIN_TOTAL])
    thr = max(HIGH_NG_MIN, 2 * med)
    red = PatternFill("solid", fgColor="FADBD8")
    for i, h in enumerate(hours, 2):
        if h.total >= HIGH_NG_MIN_TOTAL and (h.ng_pct or 0) >= thr:
            for col in range(1, 6):
                ws.cell(i, col).fill = red
    for col, w in zip("ABCDEFGH", [14, 13, 9, 10, 10, 18, 10, 16]):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"

    # --- daily
    wd = wb.create_sheet(sh["d"])
    header(wd, 1, t["daily_head"])
    for i, r in enumerate(d.days, 2):
        wd.append([r.period, r.total, r.fail_count, (r.ng_pct or 0) / 100, (r.yield_pct or 0) / 100, r.missing, r.active_hours])
        wd.cell(i, 2).number_format = "#,##0"
        for col in (4, 5):
            wd.cell(i, col).number_format = "0.00%"
    for col, w in zip("ABCDEFG", [13, 13, 9, 10, 10, 10, 16]):
        wd.column_dimensions[col].width = w

    # --- tools: summary + per hour
    wt = wb.create_sheet(sh["t"])
    header(wt, 1, t["tool_sum_head"])
    for i, x in enumerate(d.tools, 2):
        wt.append([x.sensor_id, x.tool_no, x.tool_name, x.count, x.ng_count, (x.ng_pct or 0) / 100, x.avg_value, x.min_ok_value])
        wt.cell(i, 6).number_format = "0.00%"
        wt.cell(i, 4).number_format = "#,##0"
    base = len(d.tools) + 4
    header(wt, base, t["tool_head"])
    # pivot for the per-tool chart: hour x tool NG%
    keys = sorted({(r[1], r[2], r[3]) for r in d.tool_hours}, key=lambda k: (str(k[0]), k[1]))
    for i, r in enumerate(d.tool_hours, base + 1):
        hr, sensor, tool, name, cnt, ngc, vs, vc = r
        wt.append([f"{hr[5:10].replace('-', '/')} {hr[-2:]}:00", sensor, tool, name, cnt, ngc, (ngc / cnt) if cnt else None,
                   vs / vc if vc else None])
        wt.cell(i, 7).number_format = "0.00%"
    for col, w in zip("ABCDEFGH", [16, 12, 8, 24, 12, 10, 10, 14]):
        wt.column_dimensions[col].width = w

    # --- by hour of day
    wp = wb.create_sheet(sh["p"])
    header(wp, 1, t["prof_head"])
    prof: dict[str, list] = {}
    for h in hours:
        x = prof.setdefault(h.period[-2:], [0, 0, set()])
        x[0] += h.total
        x[1] += h.fail_count
        x[2].add(h.period[:10])
    for i, k in enumerate(sorted(prof), 2):
        tt, nn, days = prof[k]
        wp.append([f"{k}:00", tt, nn, nn / tt if tt else 0, len(days)])
        wp.cell(i, 4).number_format = "0.00%"
        wp.cell(i, 2).number_format = "#,##0"
    for col, w in zip("ABCDE", [15, 13, 9, 10, 18]):
        wp.column_dimensions[col].width = w

    # --- reasons
    wr = wb.create_sheet(sh["r"])
    header(wr, 1, t["reason_head"])
    for rsn, n in d.reasons:
        wr.append([rsn, n])
    wr.column_dimensions["A"].width = 70
    wr.column_dimensions["B"].width = 10

    # --- drift
    wx = wb.create_sheet(sh["x"])
    header(wx, 1, t["drift_head"])
    flagged = " ".join(_drift_flags(d, s))
    for r in d.drift:
        who = f"{r['sensor']} tool {r['tool']} {r['name'] or ''}".strip()
        state = t["cannot"] + f": {r['skip']}" if r["skip"] else (t["drift_flag"] if f"{r['sensor']} tool {r['tool']} " in flagged + " " else t["ok"])
        wx.append([who, r["recent_avg"], r["base_avg"], (r["recent_ng"] or 0) / 100 if r["recent_ng"] is not None else None,
                   (r["base_ng"] / 100) if r["base_ng"] is not None else None, state])
        i = wx.max_row
        for col in (4, 5):
            wx.cell(i, col).number_format = "0.00%"
        for col in (2, 3):
            wx.cell(i, col).number_format = "0.0"
    wx.column_dimensions["A"].width = 34
    for col in "BCDE":
        wx.column_dimensions[col].width = 14
    wx.column_dimensions["F"].width = 50

    # --- sample pictures
    wi = None
    if d.samples:
        from io import BytesIO

        from openpyxl.drawing.image import Image as XLImage
        from PIL import Image
        wi = wb.create_sheet(sh["i"])
        header(wi, 1, t["sample_head"])
        row = 2
        for ts, sensor, reason, p in d.samples:
            try:
                with Image.open(p) as im:
                    im.thumbnail((320, 240))
                    buf = BytesIO()
                    im.convert("RGB").save(buf, "PNG")
                buf.seek(0)
                img = XLImage(buf)
            except Exception:  # noqa: BLE001 - an unreadable picture is skipped, not fatal
                continue
            wi.cell(row, 1, ts)
            wi.cell(row, 2, sensor)
            c = wi.cell(row, 3, reason)
            c.alignment = Alignment(wrap_text=True, vertical="top")
            wi.add_image(img, f"D{row}")
            wi.row_dimensions[row].height = 185
            row += 1
        wi.column_dimensions["A"].width = 20
        wi.column_dimensions["B"].width = 10
        wi.column_dimensions["C"].width = 60
        wi.column_dimensions["D"].width = 48

    # --- summary sheet
    sm = wb.create_sheet(sh["s"], 0)
    sm.sheet_view.showGridLines = False
    sm["A1"] = t["title"]
    sm["A1"].font = Font(bold=True, size=18, color=BLUE)
    a = hours[0].period[:10] if hours else d.start
    b = hours[-1].period[:10] if hours else d.end
    sm["A2"] = t["range"].format(a=a, b=b, n=len(hours)) + f" | generated {now:%Y-%m-%d %H:%M}"
    sm["A2"].font = Font(color="666666")
    miss_all = [h.missing for h in hours if h.missing is not None]
    times_all = [h.avg_time_ms for h in hours if h.avg_time_ms]
    vals = [(total, "#,##0"), (ng, "#,##0"), (avg_ng, "0.00%"), (1 - avg_ng if total else 0, "0.00%"),
            (sum(miss_all) if miss_all else None, "#,##0"), (sum(times_all) / len(times_all) if times_all else None, "0.0")]
    for col, (lab, (v, fmt)) in enumerate(zip(t["kpi"], vals), 1):
        c = sm.cell(4, col, lab)
        c.font, c.alignment, c.fill = Font(size=9, color="666666"), Alignment(horizontal="center", wrap_text=True), PatternFill("solid", fgColor=GREY)
        c = sm.cell(5, col, v)
        c.number_format, c.alignment, c.fill = fmt, Alignment(horizontal="center"), PatternFill("solid", fgColor=GREY)
        c.font = Font(bold=True, size=16, color=RED if col == 3 else BLUE)
        sm.column_dimensions[chr(64 + col)].width = 19
    sm.row_dimensions[4].height, sm.row_dimensions[5].height = 28, 30
    sm["A7"] = t["summary"]
    sm["A7"].font = Font(bold=True, size=13, color=BLUE)
    row = 8
    for head, body in findings(d, s, lang):
        sm.cell(row, 1, head).font = Font(bold=True)
        sm.cell(row, 1).alignment = Alignment(vertical="top", wrap_text=True)
        sm.merge_cells(start_row=row, start_column=2, end_row=row, end_column=6)
        c = sm.cell(row, 2, body)
        c.alignment = Alignment(wrap_text=True, vertical="top")
        lines = sum(max(1, -(-len(x) // 88)) for x in body.split("\n"))
        sm.row_dimensions[row].height = max(20, 15.5 * lines + 4)
        fill = AMBER if head in (t["h_low"], t["h_high"], t["h_pattern"], t["h_drift"]) and body not in (
            t["low_none"], t["high_none"], t["pattern_none"], t["pattern_short"], t["drift_none"]) else None
        for col in range(1, 7):
            sm.cell(row, col).border = line
            if fill:
                sm.cell(row, col).fill = PatternFill("solid", fgColor=fill)
        row += 1

    # --- charts
    g = wb.create_sheet(sh["g"], 1)
    g.sheet_view.showGridLines = False
    g["A1"] = sh["g"]
    g["A1"].font = Font(bold=True, size=16, color=BLUE)
    ct = t["charts"]
    if hours:
        cats = Reference(ws, min_col=1, min_row=2, max_row=last_h)
        many = len(hours) > HOURLY_COLUMN_LIMIT
        W = 26
        pos = [3]

        def place(chart, h=8.5, w=W):
            chart.height, chart.width = h, w
            chart.y_axis.delete = False
            chart.x_axis.delete = False
            g.add_chart(chart, f"A{pos[0]}")
            pos[0] += int(h * 2.1) + 2

        c1 = (LineChart() if many else BarChart())
        if not many:
            c1.type = "col"
        c1.title = ct[0]
        c1.add_data(Reference(ws, min_col=2, min_row=1, max_row=last_h), titles_from_data=True)
        c1.set_categories(cats)
        c1.legend = None
        c1.y_axis.scaling.min = 0
        if not many:
            c1.series[0].graphicalProperties.solidFill = BLUE
        place(c1)

        c2 = BarChart()
        c2.type = "col"
        c2.title = ct[1]
        c2.add_data(Reference(ws, min_col=4, min_row=1, max_row=last_h), titles_from_data=True)
        c2.set_categories(cats)
        c2.series[0].graphicalProperties.solidFill = RED
        c2.y_axis.number_format = "0.0%"
        c2.y_axis.scaling.min = 0
        ln = LineChart()
        ln.add_data(Reference(ws, min_col=8, min_row=1, max_row=last_h), titles_from_data=True)
        ln.series[0].graphicalProperties.line.solidFill = "333333"
        ln.series[0].graphicalProperties.line.dashStyle = "dash"
        ln.series[0].smooth = False
        c2 += ln
        c2.legend.position = "b"
        place(c2)

        if not many:
            c3 = BarChart()
            c3.type = "col"
            c3.title = ct[2]
            c3.add_data(Reference(ws, min_col=3, min_row=1, max_row=last_h), titles_from_data=True)
            c3.set_categories(cats)
            c3.legend = None
            c3.series[0].graphicalProperties.solidFill = RED
            c3.dataLabels = DataLabelList()
            c3.dataLabels.showVal = True
            place(c3)

        if len(d.days) >= 2:
            c4 = BarChart()
            c4.type = "col"
            c4.title = ct[3]
            c4.add_data(Reference(wd, min_col=4, min_row=1, max_row=len(d.days) + 1), titles_from_data=True)
            c4.set_categories(Reference(wd, min_col=1, min_row=2, max_row=len(d.days) + 1))
            c4.legend = None
            c4.series[0].graphicalProperties.solidFill = RED
            c4.y_axis.number_format = "0.00%"
            c4.y_axis.scaling.min = 0
            c4.dataLabels = DataLabelList()
            c4.dataLabels.showVal = True
            place(c4, w=14)

        # per-tool NG% by hour: one series per tool, from a pivot written next to the tool table
        if d.tool_hours and keys and len(keys) <= 6:
            hours_sorted = sorted({r[0] for r in d.tool_hours})
            col0 = 10
            wt.cell(1, col0, "pivot " + ct[4]).font = Font(italic=True, color="999999")
            wt.cell(2, col0, t["tool_head"][0]).font = Font(bold=True)
            for j, k in enumerate(keys, 1):
                wt.cell(2, col0 + j, f"{k[0]} tool {k[1]}").font = Font(bold=True)
            idx = {(r[0], r[1], r[2]): r for r in d.tool_hours}
            for i, hr in enumerate(hours_sorted, 3):
                wt.cell(i, col0, f"{hr[5:10].replace('-', '/')} {hr[-2:]}:00")
                for j, k in enumerate(keys, 1):
                    r = idx.get((hr, k[0], k[1]))
                    c = wt.cell(i, col0 + j, (r[5] / r[4]) if r and r[4] else None)
                    c.number_format = "0.00%"
            c5 = LineChart()
            c5.title = ct[4]
            n = len(hours_sorted)
            c5.add_data(Reference(wt, min_col=col0 + 1, max_col=col0 + len(keys), min_row=2, max_row=2 + n), titles_from_data=True)
            c5.set_categories(Reference(wt, min_col=col0, min_row=3, max_row=2 + n))
            c5.y_axis.number_format = "0.0%"
            c5.y_axis.scaling.min = 0
            c5.legend.position = "b"
            place(c5)

        if len(prof) >= 3:
            c6 = BarChart()
            c6.type = "col"
            c6.title = ct[5]
            c6.add_data(Reference(wp, min_col=4, min_row=1, max_row=len(prof) + 1), titles_from_data=True)
            c6.set_categories(Reference(wp, min_col=1, min_row=2, max_row=len(prof) + 1))
            c6.legend = None
            c6.series[0].graphicalProperties.solidFill = RED
            c6.y_axis.number_format = "0.0%"
            c6.y_axis.scaling.min = 0
            place(c6)

        if d.reasons:
            c7 = BarChart()
            c7.type = "bar"
            c7.title = ct[6]
            n = min(len(d.reasons), 10)
            c7.add_data(Reference(wr, min_col=2, min_row=1, max_row=n + 1), titles_from_data=True)
            c7.set_categories(Reference(wr, min_col=1, min_row=2, max_row=n + 1))
            c7.legend = None
            c7.series[0].graphicalProperties.solidFill = BLUE
            c7.dataLabels = DataLabelList()
            c7.dataLabels.showVal = True
            place(c7, h=9)

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path
