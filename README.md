# IV4 Data Agent

ระบบสำหรับรับข้อมูล Inspection จาก **KEYENCE IV4 G500CA** แบบอัตโนมัติ โดยรับไฟล์จาก Sensor ได้แก่ Image (`JPG/JPEG`) และข้อมูลผลการตรวจสอบ (`TXT`) จากนั้นทำการจับคู่ข้อมูล, Parse ข้อมูล, วิเคราะห์ผล และบันทึกลงฐานข้อมูล

> **Current Status:** Core Realtime Pipeline พร้อม Production (hardened, 28 automated tests) — รอทดสอบกับข้อมูลจริงจาก IV4  
> **Google Drive Integration:** พร้อมใช้งาน (ปิดไว้เป็นค่าเริ่มต้น — ตั้ง `IV4_UPLOAD_ENABLED=true` หลังเชื่อมบัญชี)

---

## 1. Project Overview

IV4 Data Agent เป็นระบบสำหรับรับและประมวลผลข้อมูล Inspection จาก **KEYENCE IV4 G500CA**

ระบบทำงานบน **Mini PC** ที่เชื่อมต่อกับ Sensor ผ่าน LAN โดย Sensor จะสร้างไฟล์ผลการตรวจสอบไว้ในพื้นที่ที่กำหนดบน Mini PC

ไฟล์หลักที่ระบบรองรับ ได้แก่

- JPG
- JPEG
- TXT
- Result TXT

ระบบจะตรวจจับไฟล์ใหม่แบบ Realtime และนำข้อมูลของ Inspection เดียวกันมารวมกัน ก่อนเข้าสู่กระบวนการ Parse, Analysis และ Database

---

## 2. System Objective

ระบบมีเป้าหมายดังนี้

- รับข้อมูลจาก KEYENCE IV4 แบบอัตโนมัติ
- ตรวจจับไฟล์ใหม่แบบ Realtime
- ตรวจสอบว่าไฟล์ถูกเขียนเสร็จสมบูรณ์แล้ว
- จับคู่ไฟล์ Image และ TXT ที่เป็น Inspection เดียวกัน
- Parse ข้อมูลจาก TXT
- สร้าง Inspection Record
- วิเคราะห์ผล Inspection
- บันทึกข้อมูลลง Database
- ป้องกันการบันทึก Inspection ซ้ำ
- รองรับการจัดเก็บข้อมูลบน Online Storage
- รองรับการทำ Data Analysis
- รองรับการสร้าง Dashboard ในอนาคต

---

# 3. System Architecture

ภาพรวมของระบบปัจจุบัน:

```text
                    ┌─────────────────────┐
                    │   KEYENCE IV4       │
                    │      G500CA         │
                    └──────────┬──────────┘
                               │
                               │ LAN
                               │
                               ▼
                    ┌─────────────────────┐
                    │       Mini PC       │
                    │                     │
                    │    IV4 Data Agent   │
                    └──────────┬──────────┘
                               │
                               │ JPG + TXT
                               ▼
                    ┌─────────────────────┐
                    │    data/incoming    │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │       Watcher       │
                    │  File Monitoring    │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │       Matcher       │
                    │ Inspection Matching │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │       Parser        │
                    │      TXT Data       │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │      Analysis       │
                    │   Business Rules    │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │      Database       │
                    │       SQLite        │
                    └─────────────────────┘
```

---

# 4. Realtime Data Flow

ข้อมูลจาก IV4 จะเข้าสู่ระบบตามลำดับ:

```text
IV4 G500CA
    │
    │
    ├── 001.jpg
    ├── 001.txt
    └── 001_result.txt
    │
    ▼
data/incoming/
    │
    ▼
Watcher
    │
    ▼
File Stability Check
    │
    ▼
Matcher
    │
    ▼
Inspection Group
    │
    ▼
Processing
    │
    ├── TXT Parser
    │
    ├── Inspection Record
    │
    ├── Analysis Engine
    │
    └── Database
    │
    ▼
DONE
```

---

# 5. Project Structure

```text
iv4-data-agent/
│
├── app/
│   ├── __init__.py
│   ├── __main__.py          # python -m app
│   ├── config.py            # settings จาก .env / env vars
│   ├── logging_setup.py     # rotating log
│   ├── pipeline.py
│   │
│   ├── ingestion/
│   │   ├── __init__.py
│   │   ├── watcher.py
│   │   ├── matcher.py
│   │   ├── stability.py
│   │   ├── lifecycle.py
│   │   └── record.py        # manifest.json + UID + content hash
│   │
│   ├── parser/
│   │   ├── __init__.py
│   │   ├── txt_parser.py
│   │   └── inspection_record.py
│   │
│   ├── analysis/
│   │   ├── __init__.py
│   │   └── analyzer.py
│   │
│   ├── database/
│   │   ├── __init__.py
│   │   └── repository.py
│   │
│   └── upload/
│       ├── __init__.py
│       ├── google_drive.py
│       ├── mock_uploader.py
│       └── queue.py
│
├── credentials/
│
├── data/
│   ├── incoming/
│   ├── processing/
│   ├── uploaded/
│   ├── error/
│   └── database/
│       └── iv4.db
│
├── logs/
│
├── deploy/
│   ├── install_service.ps1
│   ├── uninstall_service.ps1
│   └── healthcheck.ps1
│
├── scripts/
│   ├── backup_db.py
│   ├── metrics.py           # รายงาน metric / export CSV
│   ├── gdrive_auth.py       # ล็อกอิน Google Drive ครั้งเดียว
│   └── stats.py
│
├── tests/
│   ├── conftest.py
│   ├── mock_data/           # iv4/ = ไฟล์จริงจาก sensor
│   ├── test_iv4_real.py
│   ├── test_parsing.py
│   ├── test_database.py
│   ├── test_agent.py
│   └── test_google_drive.py
│
├── .env.example
├── .gitignore
├── requirements.txt
├── requirements-dev.txt
└── README.md
```

---

# 6. Directory Description

## 6.1 `app/`

เก็บ Source Code หลักของระบบ

---

## 6.2 `app/ingestion/`

รับผิดชอบการนำข้อมูลเข้าสู่ระบบ

ประกอบด้วย:

```text
watcher.py
matcher.py
lifecycle.py
record.py
```

### Watcher

ตรวจจับไฟล์ใหม่ที่เข้ามาใน:

```text
data/incoming/
```

### Matcher

ตรวจสอบว่าไฟล์ของ Inspection เดียวกันมาครบหรือไม่

### Lifecycle

จัดการสถานะของ Inspection

### Record

จัดการข้อมูล Record ที่เกี่ยวข้องกับการ Ingestion

---

## 6.3 `app/parser/`

รับผิดชอบการอ่านและแปลงข้อมูลจาก TXT

ประกอบด้วย:

```text
txt_parser.py
inspection_record.py
```

---

## 6.4 `app/analysis/`

รับผิดชอบ Business Logic และการวิเคราะห์ผล Inspection

```text
analyzer.py
```

---

## 6.5 `app/database/`

รับผิดชอบการบันทึกและอ่านข้อมูลจาก Database

```text
repository.py
```

Database ปัจจุบัน:

```text
SQLite
```

---

## 6.6 `app/upload/`

เตรียมไว้สำหรับ Online Storage

ประกอบด้วย:

```text
google_drive.py
mock_uploader.py
queue.py
```

Google Drive upload ทำงานใน thread แยก เปิดด้วย `IV4_UPLOAD_ENABLED=true` (ดูข้อ 34)

---

# 7. Data Directory

## `data/incoming/`

เป็นพื้นที่รับข้อมูลจาก IV4

ตัวอย่าง:

```text
data/incoming/

001.jpg
001.txt
001_result.txt
```

---

## `data/processing/`

เป็นพื้นที่เก็บ Inspection ที่ผ่านการจับคู่แล้ว

ตัวอย่าง:

```text
data/processing/
│
└── 001/
    ├── 001.jpg
    ├── 001.txt
    ├── 001_result.txt
    └── manifest.json        # ใช้ชื่อ folder = UID เช่น 001__20261003T074846246028
```

---

## `data/uploaded/`

พื้นที่สำหรับเก็บข้อมูลที่ Upload ไปยัง Online Storage สำเร็จ

ปัจจุบันเตรียมไว้สำหรับการพัฒนาในอนาคต

---

## `data/error/`

พื้นที่สำหรับ Inspection ที่เกิดข้อผิดพลาดระหว่าง Processing

---

## `data/database/`

เก็บ SQLite Database

```text
data/database/iv4.db
```

---

# 8. Supported File Types

ปัจจุบันระบบรองรับ:

```text
.jpg
.jpeg
.txt
```

Inspection หนึ่งรายการ (IV4 จริง):

```text
00001_03102026_181913.jpg
00001_03102026_181913.txt
```

รูปแบบ mock เดิม (`001.jpg` + `001.txt` + `001_result.txt`) ยังใช้ได้โดยตั้ง `IV4_EXPECTED_TEXTS=2`

---

# 9. Inspection ID และ UID

ระบบใช้ชื่อไฟล์ในการจัดกลุ่ม (Group ID) โดยตัด suffix `_result` ออก

```text
007.jpg / 007.txt / 007_result.txt          -> Group ID = 007
CAM1_0001.jpg / CAM1_0001_result.txt        -> Group ID = CAM1_0001
```

> เดิมใช้ `stem.split("_")[0]` ซึ่งจะรวม Inspection ที่ต่างกันเข้าด้วยกันทันทีที่ชื่อไฟล์จริงมี `_`

เนื่องจากตัวนับไฟล์ของ IV4 **อาจรีเซ็ตหรือวนซ้ำ** ได้ (ปิด-เปิดเครื่อง, counter เต็ม) ชื่อไฟล์จึงไม่ unique
ทุก Inspection ที่รับเข้ามาจะได้ **UID** = Group ID + เวลาที่รับ (UTC):

```text
007__20261003T074846246028
```

UID ใช้เป็นชื่อ folder ใน `data/processing/` และเป็น key หลักใน Database

---

# 10. File Matching

Matcher จะตรวจสอบจำนวนไฟล์ของแต่ละ Inspection

ตัวอย่างกรณีไฟล์ครบ:

```text
[MATCH GROUP] id=007

  - 007.jpg
  - 007.txt
  - 007_result.txt

  Images : 1
  Texts  : 2
  STATUS : COMPLETE
```

---

## 10.1 WAITING

ถ้าไฟล์ยังไม่ครบ:

```text
[MATCH GROUP] id=007

  - 007.jpg

  Images : 1
  Texts  : 0
  STATUS : WAITING
```

ระบบจะรอไฟล์ที่เหลือ

---

## 10.2 COMPLETE

เมื่อไฟล์ครบ:

```text
Images : 1
Texts  : 2
STATUS : COMPLETE
```

จากนั้นจะเข้าสู่ Pipeline

---

## 10.3 INVALID

ไฟล์เกินจำนวน (เช่น Image 2 ไฟล์, TXT ซ้ำ) หรือไม่มี TXT หลัก → ย้ายไป `data/error/<uid>/` พร้อม `ERROR.txt`

## 10.4 INCOMPLETE (Timeout)

ถ้ากลุ่มยังไม่ครบภายใน `IV4_GROUP_TIMEOUT` (default 120 วินาที) → ย้ายไป `data/error/` ไม่ค้างใน `incoming/` ตลอดไป

---

# 11. File Stability Check

ไฟล์จะถูกประมวลผลเมื่อ **ขนาดและเวลาแก้ไขไม่เปลี่ยน** ต่อเนื่องอย่างน้อย `IV4_SETTLE_SECONDS` (default 1.5 วินาที)

การตรวจทำแบบ non-blocking ในรอบ scan (ไม่ sleep ใน watchdog callback แบบเดิม) และ:

* ไฟล์ที่ยังถูก IV4/FTP เปิดอยู่ (Windows lock) จะถูก rollback แล้วลองใหม่รอบถัดไป
* JPG ทุกไฟล์ถูกเปิดตรวจด้วย Pillow (`IV4_VERIFY_IMAGES`) เพื่อจับไฟล์ที่ส่งมาไม่ครบ

---

# 12. TXT Parser — รูปแบบจริงจาก KEYENCE IV4-G500CA

ไฟล์จริงจาก FTP (ยืนยันแล้ว 2026-10-03): **1 inspection = รูป 1 + TXT 1** ชื่อเดียวกัน

```text
00001_03102026_181913.jpg
00001_03102026_181913.txt      <ลำดับ>_<DDMMYYYY>_<HHMMSS>
```

> เลขลำดับ (`00001`) **รีเซ็ตได้** — ใช้ชื่อไฟล์ทั้งชื่อเป็น Inspection ID และเก็บ `Trigger No.` แยก

เนื้อหา TXT (คั่นด้วย Tab, ASCII, CRLF):

```text
Time and Date	03/10/2026	18:19:13
Program No.	0
Trigger No.	301512
TIME[ms]	36
Total Status	OK
Tool01:AI Differentiate	OK	100
Tool02:AI Differentiate	OK	100
```

Parser ตรวจรูปแบบอัตโนมัติ (IV4 tab / `key=value` แบบ mock เดิม) และแปลงเป็น:

| TXT | Database |
| --- | --- |
| ชื่อไฟล์ | `inspection_id` |
| Time and Date (`IV4_DATE_FORMAT`, default DD/MM/YYYY) | `timestamp` = `2026-10-03 18:19:13` |
| Program No. | `program_no` |
| Trigger No. | `trigger_no` |
| TIME[ms] | `inspection_time_ms` |
| Total Status | `result` |
| ToolNN: ชื่อ / สถานะ / ค่า | `tools_json`; `score` = ค่าต่ำสุดของทุก tool; `defect_count` = จำนวน tool ที่ NG |
| (ตั้งใน `.env`) | `camera_id` = `IV4_SENSOR_ID`, `machine_id` = `IV4_MACHINE_ID` |

บรรทัดที่ไม่รู้จักจะถูกเก็บไว้ใน `raw_data` — ไม่มีข้อมูลหาย

ไฟล์ตัวอย่างจริงอยู่ที่ `tests/mock_data/iv4/`

---

# 13. Analysis Rule (ข้อมูลจริง)

ใช้ **ผลตัดสินของ sensor** เป็นหลัก:

* `Total Status = NG` หรือ tool ใดเป็น NG → **FAIL** (reason ระบุ tool เช่น `Tool02:AI Differentiate NG (value=12)`)
* OK ทุกอย่าง → **PASS**
* ไม่มี `Total Status` → **UNKNOWN**
* เกณฑ์เพิ่มเติม (ปิดไว้): `IV4_SCORE_THRESHOLD` = FAIL ถ้าค่า tool ต่ำสุดน้อยกว่าค่านี้

---

# 14. ส่งข้อมูลทุกชิ้น (Good + NG) — ความจุและ Metric

## 14.1 ผลทดสอบที่อัตราจริง (20 ชิ้น/วินาที, รูป 110 KB)

| ทดสอบ | ผล |
| --- | --- |
| 1,801 ชิ้นใน 90 วินาที บน DB ที่มีอยู่แล้ว 100,000 แถว | เข้า DB ครบ 1,801/1,801, ไฟล์ถึง DB median 1.8 s / max 2.1 s, ไม่มี backlog สะสม |
| ความเร็ว insert ลง DB | ~700 ชิ้น/วินาที (เผื่อ ~35 เท่า) |
| Upload NG ขนานไปด้วย | ส่งเฉพาะ NG 36/36, ไม่กระทบการรับข้อมูล |

## 14.2 ปริมาณข้อมูล (ต้องวางแผน storage)

| | ต่อวัน (24 ชม.) | ต่อกะ 8 ชม. |
| --- | --- | --- |
| รูป | ~190 GB | ~63 GB |
| Database | ~1 GB | ~0.3 GB |

ตั้ง `IV4_ARCHIVE_DIR` ไปที่ดิสก์ใหญ่/NAS และตั้ง retention เช่น

```text
IV4_RETENTION_OK_DAYS=3      # รูป OK เก็บ 3 วัน
IV4_RETENTION_NG_DAYS=180    # รูป NG เก็บ 6 เดือน
IV4_RETENTION_ROWS_DAYS=90   # แถวรายชิ้นใน DB 90 วัน (metric รายชั่วโมงเก็บตลอด)
```

ระบบเตือนเมื่อดิสก์เหลือน้อยกว่า `IV4_MIN_FREE_GB` (log + `health.json` + `healthcheck.ps1`)

## 14.3 โครงสร้างไฟล์

```text
data/processing/   <- เฉพาะที่กำลังประมวลผล (ปกติว่าง)
data/archive/
└── 2026-10-03/          (วันที่ตามเวลา sensor)
    ├── OK/<uid>/
    ├── NG/<uid>/
    └── UNKNOWN/<uid>/
```

Retention ลบทีละ folder วัน/สถานะ — เร็วและไม่กระทบการรับข้อมูล (ทำใน thread แยก)

## 14.4 Metric สำหรับนำเสนอ

ตาราง `hourly_stats` / `hourly_tool_stats` อัปเดตทุกครั้งที่บันทึก → ดึง metric ได้ทันทีไม่ว่าข้อมูลจะมีกี่ล้านแถว

```powershell
python -m scripts.metrics                                   # วันนี้ รายชั่วโมง
python -m scripts.metrics --from 2026-10-01 --to 2026-10-08 --by day --csv week.csv
```

| Metric | ความหมาย |
| --- | --- |
| total / NG / NG % / yield % | นับจากไฟล์ที่ได้รับ |
| avg ms / max ms | Cycle time จาก `TIME[ms]` |
| **missing** | `Trigger No.` ที่ sensor นับ แต่ไม่ได้รับไฟล์ — ควรเป็น 0 ถ้า FTP ส่งครบ |
| NG by tool | Tool ไหนตัด NG เท่าไร, ค่าเฉลี่ย, **lowest OK value** (ระยะห่างก่อนจะเป็น NG) |

> ถ้า `missing` ไม่เป็น 0 แปลว่า sensor/FTP ส่งไม่ทัน — NG % จะคลาดเคลื่อน ต้องแก้ที่การตั้งค่า sensor/เครือข่ายก่อนนำตัวเลขไปใช้

---

# 15. Analysis Engine

Analysis Engine ใช้ข้อมูลจาก Inspection Record เพื่อวิเคราะห์ผล

ตัวอย่าง:

```text
Inspection ID : 007
Status        : PASS
Score         : 97.8
Defect Count  : 0
Confidence    : 0.978
```

ตัวอย่าง Analysis Reason:

```text
No failure conditions detected
```

---

# 16. Analysis Rules

Analysis Engine ถูกออกแบบให้สามารถเพิ่ม Business Rules ได้

ตัวอย่าง Rule ที่สามารถพัฒนาเพิ่มเติม:

```text
Score Threshold
Confidence Threshold
Defect Count Threshold
Inspection Time Threshold
Result Condition
Machine Condition
Camera Condition
```

ตัวอย่าง:

```text
IF result = OK
AND defect_count = 0
AND confidence >= threshold

THEN PASS
```

---

# 17. Database

ปัจจุบันใช้:

```text
SQLite
```

Database:

```text
data/database/iv4.db
```

Database มีหน้าที่เก็บข้อมูล Inspection และ Analysis Result

---

# 18. Database Functions

ระบบรองรับ:

* Create Inspection
* Update Inspection
* Read Inspection
* Duplicate Protection
* Inspection Count

---

# 19. Duplicate Protection

| กรณี | ผลลัพธ์ |
| --- | --- |
| IV4 ส่ง `001` ซ้ำแต่เป็นชิ้นงานใหม่ (counter reset) | สร้าง record ใหม่ (UID ต่างกัน) — **ประวัติเดิมไม่ถูกเขียนทับ** |
| ไฟล์ชุดเดิมเป๊ะถูก copy เข้ามาซ้ำ | ตรวจด้วย SHA-256 ของไฟล์ (`content_hash`) → `DUPLICATE` ย้ายไป `data/error/` |
| Agent crash แล้วประมวลผล folder เดิมซ้ำ | Upsert ตาม UID → `UPDATED` ไม่เกิด record ซ้ำ |

> ระบบเดิม upsert ด้วย `inspection_id` ทำให้ข้อมูลเก่าถูกเขียนทับ และไฟล์ชุดที่สองค้างใน `incoming/` ตลอดไป

---

# 20. Pipeline

`pipeline.py` ทำหน้าที่เชื่อม Component ต่าง ๆ เข้าด้วยกัน

Flow:

```text
Inspection Folder
       │
       ▼
     Parser
       │
       ▼
Inspection Record
       │
       ▼
    Analysis
       │
       ▼
    Database
```

ตัวอย่าง:

```python
process_inspection(
    inspection_id="007",
    processing_folder=PROCESSING_FOLDER,
)
```

---

# 21. Realtime Pipeline

เมื่อ Matcher พบ Inspection ที่ครบ:

```text
STATUS : COMPLETE
```

ระบบจะเรียก Pipeline อัตโนมัติ

ตัวอย่าง:

```text
[COMPLETE] group=007

[PIPELINE] inspection=007

[PARSER] inspection=007
  inspection_id = 007

[ANALYSIS] inspection=007
  status = PASS
  score = 97.8
  confidence = 0.978

[DATABASE] inspection=007
  action = CREATED
  database_id = 2
  status = PASS

[PIPELINE COMPLETE] inspection=007

[DONE] group=007
```

---

# 22. Running the System

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
copy .env.example .env          # ปรับ path / threshold
python -m app                   # หรือ python -m app.ingestion.watcher
```

หยุดด้วย Ctrl+C (ปิดอย่างปลอดภัย)

---

# 23. Startup และ Recovery

เมื่อเริ่มทำงาน Agent จะ:

1. Migrate Database เดิม (v1) อัตโนมัติ — ตารางเดิมถูกเก็บไว้เป็น `inspection_v1_<timestamp>`
2. ประมวลผล folder ใน `data/processing/` ที่ค้างสถานะ `CLAIMED` (crash / DB ล่ม)
3. Scan `data/incoming/` — ไฟล์ที่มาถึงตอน Agent ปิดอยู่จะถูกประมวลผล
4. ใช้ watchdog event เพื่อปลุก scanner ทันที + scan ทุก `IV4_SCAN_INTERVAL` วินาที (กัน event หาย)

Lifecycle ของแต่ละ Inspection ถูกบันทึกใน `data/processing/<uid>/manifest.json`

```text
incoming/ ──► processing/<uid>/ (CLAIMED) ──► DONE
                                  │
                                  ├─ DB error ──► retry ทุก 30s (สูงสุด 20 ครั้ง)
                                  └─ parse / image / duplicate error ──► error/<uid>/ + ERROR.txt
```

---

# 24. End-to-End Example

เมื่อ IV4 ส่ง:

```text
007.jpg
007.txt
007_result.txt
```

ระบบจะทำงาน:

```text
IV4
 │
 ▼
incoming/
 │
 ▼
Watcher
 │
 ▼
Stability Check
 │
 ▼
Matcher
 │
 ▼
COMPLETE
 │
 ▼
processing/007/
 │
 ▼
TXT Parser
 │
 ▼
Inspection Record
 │
 ▼
Analysis
 │
 ▼
SQLite
 │
 ▼
DONE
```

---

# 25. Testing

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Test ทั้งหมดใช้ temp folder และ temp database — **ไม่แตะ `data/` จริง**

| ไฟล์ | ครอบคลุม |
| --- | --- |
| `tests/test_parsing.py` | TXT parser (UTF-8/UTF-16/cp874), Inspection Record, Analyzer, Matcher |
| `tests/test_database.py` | Create/Update, ID ซ้ำเก็บประวัติ, Duplicate hash, Migration จาก v1 |
| `tests/test_metrics.py` | Metric รายชั่วโมง/วัน, missing จาก Trigger No., NG ตาม Tool, retention, ดิสก์ |
| `tests/test_google_drive.py` | Upload ลงโครงสร้าง วัน/UID, root folder สร้างครั้งเดียว, เน็ตหลุดแล้ว retry ไม่ซ้ำ, config ผิดไม่ทำให้ service ล่ม |
| `tests/test_agent.py` | End-to-end: ไฟล์ครบ/ไม่ครบ, counter reset, backlog ตอน startup, timeout, invalid, JPG เสีย, DB ล่มแล้ว retry, upload queue, watcher จริง 20 inspection |

---

# 26. End-to-End Test

สร้าง Mock Inspection:

```text
007.jpg
007.txt
007_result.txt
```

นำไปไว้ที่:

```text
data/incoming/
```

จากนั้น Run:

```powershell
python -m app.ingestion.watcher
```

---

# 27. Mock Data Example

## 007.txt

```text
inspection_id=007
timestamp=2026-09-28 15:00:15
machine_id=MACHINE_01
camera_id=CAM_01
result=OK
score=97.8
width=120.1
height=45.0
```

## 007_result.txt

```text
result=OK
defect_count=0
inspection_time_ms=44
confidence=0.978
```

## 007.jpg

เป็น Image สำหรับจำลอง Inspection

---

# 28. End-to-End Test Result

Mock Inspection `007` ผ่านการทดสอบแล้ว

```text
Inspection ID : 007
Status        : PASS
Score         : 97.8
Confidence    : 0.978
Database ID   : 2
```

Pipeline Result:

```text
Watcher       PASS
Matcher       PASS
Parser        PASS
Inspection    PASS
Analysis      PASS
Database      PASS
Pipeline      PASS
```

---

# 32. Current System Status

| Component              | Status     |
| ---------------------- | ---------- |
| File Watcher + Scanner | PASS       |
| File Stability Check   | PASS       |
| File Matcher           | PASS       |
| TXT Parser             | PASS       |
| Inspection Record      | PASS       |
| Analysis Engine        | PASS (mock rules) |
| SQLite (WAL)           | PASS       |
| Duplicate Protection   | PASS       |
| Error Quarantine       | PASS       |
| Crash Recovery         | PASS       |
| Rotating Log + Health  | PASS       |
| Windows Service script | READY      |
| Google Drive Upload    | READY (ปิดไว้จนกว่าจะเชื่อมบัญชี) |
| Real IV4 Data          | PASS (5 ไฟล์จริง, OK เท่านั้น) |

---

# 33. Current Architecture

```text
┌─────────────────────────────┐
│       KEYENCE IV4 G500CA    │
└──────────────┬──────────────┘
               │
               │ LAN
               ▼
┌─────────────────────────────┐
│           Mini PC           │
│                             │
│       IV4 Data Agent        │
└──────────────┬──────────────┘
               │
               ▼
┌─────────────────────────────┐
│       data/incoming         │
│                             │
│ JPG + TXT + Result TXT      │
└──────────────┬──────────────┘
               │
               ▼
┌─────────────────────────────┐
│          Watcher            │
└──────────────┬──────────────┘
               │
               ▼
┌─────────────────────────────┐
│          Matcher            │
└──────────────┬──────────────┘
               │
               ▼
┌─────────────────────────────┐
│           Parser            │
└──────────────┬──────────────┘
               │
               ▼
┌─────────────────────────────┐
│          Analysis           │
└──────────────┬──────────────┘
               │
               ▼
┌─────────────────────────────┐
│           SQLite            │
│          iv4.db             │
└─────────────────────────────┘
```

---

# 34. Online Storage — Google Drive

> **Google API key ใช้อัปโหลดไม่ได้** (อ่านได้เฉพาะไฟล์ public) ต้องใช้ OAuth หรือ Service Account

Upload ทำงานใน thread แยกจาก ingestion — เน็ตช้าหรือหลุดจะไม่กระทบการรับข้อมูล
ส่งเฉพาะ inspection ที่สถานะ `DONE` และยังไม่ upload; ถ้า error จะ retry รอบถัดไป (ไม่เกิดไฟล์ซ้ำใน Drive)

โครงสร้างใน Drive:

```text
IV4 Data Agent/
└── 2026-10-03/
    └── 007__20261003T074846246028/
        ├── 007.jpg
        ├── 007.txt
        ├── 007_result.txt
        └── manifest.json
```

## 34.1 ตั้งค่าแบบ OAuth (Gmail ส่วนตัว)

1. [Google Cloud Console](https://console.cloud.google.com/) → เปิด **Google Drive API**
2. **OAuth consent screen** → External → เพิ่ม Gmail ของคุณเป็น *Test user*
   (หรือ Publish app — ถ้าอยู่ในโหมด Testing token จะหมดอายุทุก 7 วัน)
3. **Credentials → Create credentials → OAuth client ID → Desktop app** → Download JSON
4. บันทึกเป็น `credentials/client_secret.json`
5. บน Mini PC รันครั้งเดียว (เปิด browser ให้ล็อกอิน):

   ```powershell
   python -m scripts.gdrive_auth --test
   ```

   ได้ `credentials/token.json` และ folder `IV4 Data Agent` ใน Drive
6. ตั้ง `.env`: `IV4_UPLOAD_ENABLED=true` แล้ว restart service

Scope ที่ใช้คือ `drive.file` — Agent เห็นและแก้ได้เฉพาะไฟล์ที่ตัวเองสร้าง ไม่เข้าถึงไฟล์อื่นใน Drive

## 34.2 ตั้งค่าแบบ Service Account (Google Workspace)

Service Account ไม่มีพื้นที่ใน My Drive ต้องใช้ **Shared Drive**

```text
IV4_GDRIVE_AUTH=service_account
IV4_GDRIVE_CREDENTIALS=credentials/service-account.json
IV4_GDRIVE_FOLDER_ID=<id ของ folder ใน Shared Drive ที่ share ให้ service account เป็น Content manager>
```

## 34.3 ตรวจสถานะ

`logs/health.json` → `upload.pending`, `upload.uploaded`, `upload.last_upload_error`
และ `manifest.json` ของแต่ละ inspection มี `uploaded_at` / `upload_ref` (Drive folder id)

> ไฟล์ใน `credentials/` ห้าม commit (อยู่ใน `.gitignore` แล้ว)

---

# 35. Future Data Analysis

เมื่อมีข้อมูลจริงจาก IV4 จำนวนมากขึ้น สามารถนำข้อมูลใน Database มาวิเคราะห์ได้ เช่น:

## Quality Analysis

* PASS Rate
* FAIL Rate
* Result Distribution
* Score Distribution
* Confidence Distribution

## Defect Analysis

* Defect Frequency
* Defect Type
* Defect Trend
* Defect Rate

## Performance Analysis

* Average Inspection Time
* Maximum Inspection Time
* Minimum Inspection Time
* Inspection Throughput

## Time Series Analysis

* Hourly Trend
* Daily Trend
* Weekly Trend
* Monthly Trend

## Machine Analysis

* Machine Performance
* Machine PASS Rate
* Machine FAIL Rate

## Camera Analysis

* Camera Performance
* Camera Result Distribution

---

# 36. Future Dashboard

สามารถสร้าง Dashboard สำหรับแสดงข้อมูล Realtime เช่น:

```text
┌─────────────────────────────────────────────┐
│              IV4 Dashboard                  │
├─────────────────────────────────────────────┤
│                                             │
│  Total Inspection       10,542              │
│                                             │
│  PASS                   10,112              │
│  FAIL                      430              │
│                                             │
│  PASS Rate                95.92%             │
│                                             │
├─────────────────────────────────────────────┤
│                                             │
│  Score Trend                               │
│                                             │
│  ──────────────────────────────────────     │
│                                             │
├─────────────────────────────────────────────┤
│                                             │
│  Defect Trend                              │
│                                             │
│  ──────────────────────────────────────     │
│                                             │
└─────────────────────────────────────────────┘
```

---

# 37. Future Production Architecture

เมื่อระบบพร้อม Production Architecture สามารถเป็น:

```text
                    KEYENCE IV4
                         │
                         │ LAN
                         ▼
                    ┌─────────┐
                    │ Mini PC │
                    └────┬────┘
                         │
                         ▼
                 ┌───────────────┐
                 │ IV4 Data Agent│
                 └───────┬───────┘
                         │
              ┌──────────┼──────────┐
              │          │          │
              ▼          ▼          ▼
           SQLite      Storage    Logs
              │          │
              │          ▼
              │      Cloud Storage
              │
              ▼
        Data Analysis
              │
              ▼
          Dashboard
```

---

# 38. Error Handling

ทุกกรณีที่ประมวลผลไม่ได้จะถูกย้ายไป `data/error/<uid>/` พร้อม `ERROR.txt` (เวลา + เหตุผล) — **ไม่มีการลบไฟล์**

| เหตุ | การจัดการ |
| --- | --- |
| ไฟล์ไม่ครบภายใน timeout | error `INCOMPLETE` |
| ไฟล์เกิน / ซ้ำ | error `INVALID group` |
| JPG เสีย / ส่งไม่ครบ | error `IMAGE` |
| TXT อ่านไม่ได้ | error `PARSER` |
| ค่าตัวเลขผิดรูปแบบ | ไม่ fail — เก็บเป็น warning ใน `parse_warnings` และค่าดิบใน `raw_data` |
| ไม่มี field `result` | `UNKNOWN` (ไม่ถือว่า PASS) |
| Database lock / ล่ม | คงไว้ใน `processing/` แล้ว retry |
| ไฟล์ชุดเดิมซ้ำ | error `DUPLICATE` |

---

# 39. Logging และ Health

* `logs/iv4_agent.log` — rotating log (10 MB × 10 ไฟล์) มี timestamp ทุกบรรทัด
* `logs/health.json` — heartbeat ทุกรอบ scan: จำนวน processed / pass / fail / errors, ไฟล์ค้างใน incoming, error ล่าสุด
* `deploy/healthcheck.ps1` — ตรวจ service, heartbeat, backlog, error ใหม่ → เขียน Windows Event Log เมื่อผิดปกติ

---

# 40. Security

ข้อมูล Sensitive ต้องไม่ถูก Commit เข้า Git

ตัวอย่าง:

```text
.env
credentials/
*.json
```

โดยเฉพาะ:

* API Keys
* Access Tokens
* Google Drive Credentials
* Service Account Credentials
* Passwords
* Database Credentials

---

# 41. `.gitignore`

ใช้ `.gitignore` ใน repo — `data/`, `*.db`, `logs/`, `.env`, `credentials/` ไม่ถูก commit
Mock data สำหรับ test อยู่ที่ `tests/mock_data/`

---

# 42. Development Workflow

Development Workflow ปัจจุบัน:

```text
1. IV4 Generate Files
        │
        ▼
2. Watcher Detect Files
        │
        ▼
3. Stability Check
        │
        ▼
4. Matcher
        │
        ▼
5. Move to Processing
        │
        ▼
6. Parse TXT
        │
        ▼
7. Build Inspection Record
        │
        ▼
8. Analysis
        │
        ▼
9. Save to SQLite
        │
        ▼
10. DONE
```

---

# 43. Development Roadmap

## Phase 1 — File Ingestion

```text
[COMPLETED]
```

* File Watcher
* File Stability
* File Matching
* Processing Folder

---

## Phase 2 — Data Parsing

```text
[COMPLETED]
```

* TXT Parser
* Inspection Record
* Multiple TXT Support

---

## Phase 3 — Analysis

```text
[COMPLETED]
```

* Analysis Engine
* PASS / FAIL
* Score
* Confidence
* Defect Count

---

## Phase 4 — Database

```text
[COMPLETED]
```

* SQLite
* Repository
* Create
* Update
* Read
* Duplicate Protection

---

## Phase 5 — Realtime Pipeline

```text
[COMPLETED]
```

รวม:

```text
Watcher
   ↓
Matcher
   ↓
Parser
   ↓
Analysis
   ↓
Database
```

---

## Phase 6 — Real IV4 Data

```text
[NEXT]
```

นำข้อมูลจริงจาก:

```text
KEYENCE IV4 G500CA
```

มาทดสอบกับระบบ

ตรวจสอบ:

* Filename
* File Naming Pattern
* TXT Format
* Result Format
* Data Frequency
* Inspection Frequency
* File Creation Timing
* Multiple Camera Behavior

---

## Phase 7 — Online Storage

```text
[READY — รอเชื่อมบัญชี Google บน Mini PC]
```

Google Drive (OAuth / Service Account) — ดูข้อ 34

---

## Phase 8 — Data Analysis

```text
[PLANNED]
```

สร้าง Analysis Layer สำหรับข้อมูลจำนวนมาก

---

## Phase 9 — Dashboard

```text
[PLANNED]
```

Realtime Monitoring และ Historical Analysis

---

## Phase 10 — Production Deployment

```text
[READY — รอติดตั้งบน Mini PC]
```

* Windows Service (NSSM) + Auto Start + Auto Restart — `deploy/install_service.ps1`
* Health Check + Windows Event Log — `deploy/healthcheck.ps1`
* Error Recovery — manifest + retry + startup recovery
* Log Rotation — `logs/iv4_agent.log`
* Backup — `python -m scripts.backup_db --keep 30`
* Summary — `python -m scripts.stats`

---

# 44. Production Deployment Checklist (Mini PC)

1. ติดตั้ง Python 3.11+ และ [NSSM](https://nssm.cc)
2. `python -m venv .venv` → `pip install -r requirements.txt`
3. `copy .env.example .env` แล้วตั้ง `IV4_INCOMING_DIR` ให้ตรงกับ folder ที่ IV4/FTP เขียนไฟล์
4. ถ้าเป็น network share ตั้ง `IV4_USE_POLLING=true`
5. `python -m pytest -q` ต้องผ่านทั้งหมดบนเครื่องจริง
6. ทดสอบมือ: `python -m app` แล้ววางไฟล์ตัวอย่างจาก `tests/mock_data/`
7. Admin PowerShell: `.\deploy\install_service.ps1 -Nssm C:\tools\nssm\win64\nssm.exe`
8. Task Scheduler:
   * ทุก 5 นาที: `powershell -File deploy\healthcheck.ps1`
   * ทุกวัน: `.venv\Scripts\python.exe -m scripts.backup_db --keep 30` (ควร copy `backups/` ออกนอกเครื่อง)
9. ตั้ง Windows Update / Power plan ไม่ให้เครื่อง sleep และกำหนดเวลา restart นอกช่วงผลิต
10. ตรวจพื้นที่ disk: รูปจาก IV4 สะสมใน `data/processing/` — ต้องมีนโยบาย archive/ลบ (ดูข้อ 45)

---

# 45. Current Limitations / Next Step

ต้องทำก่อนเปิดใช้งานจริงเต็มรูปแบบ:

1. **ข้อมูลจริงจาก IV4** — ✅ รองรับรูปแบบ TXT จริงแล้ว (ข้อ 12) — ยังต้องยืนยันว่า **ชื่อไฟล์รูปตรงกับ TXT** และขอตัวอย่าง **NG จริง**
2. **ส่งทุกชิ้น** — ระบบรองรับแล้ว (ข้อ 14) ต้องตั้ง sensor ให้ส่งทุกชิ้น, เตรียม storage และตั้ง retention; ตรวจ `missing` = 0
3. **Retention** — ยังไม่มีการ archive/ลบรูปเก่า; ประเมินขนาดรูป × จำนวนต่อวัน แล้วกำหนดนโยบาย
4. **Online Storage** — Google Drive พร้อมแล้ว ต้องสร้าง OAuth client + รัน `scripts.gdrive_auth` บน Mini PC (ข้อ 34)
5. **Multi-camera** — ถ้า IV4 หลายตัวเขียนลง folder เดียวกันด้วยเลขเดียวกัน ต้องแยก folder หรือใส่ prefix กล้องในชื่อไฟล์
6. Dashboard / Data Analysis

---

# 46. Project Status Summary

Current:

```text
┌──────────────────────────────────────┐
│          IV4 DATA AGENT              │
├──────────────────────────────────────┤
│                                      │
│ File Watcher             ✅          │
│ Stability Check          ✅          │
│ File Matcher             ✅          │
│ TXT Parser               ✅          │
│ Inspection Record        ✅          │
│ Analysis Engine          ✅          │
│ SQLite Database          ✅          │
│ Duplicate Protection     ✅          │
│ Realtime Pipeline        ✅          │
│ End-to-End Mock Test     ✅          │
│                                      │
│ Real IV4 Data            ⏳          │
│ Google Drive             ✅ (รอเชื่อม) │
│ Data Analysis            🔜          │
│ Dashboard                🔜          │
│ Production Deployment    🔜          │
│                                      │
└──────────────────────────────────────┘
```

---

# 47. Conclusion

IV4 Data Agent ถูกออกแบบให้เป็นระบบกลางสำหรับรับข้อมูลจาก KEYENCE IV4 G500CA และนำข้อมูล Inspection มาประมวลผลแบบอัตโนมัติ

Core Pipeline ในปัจจุบันสามารถทำงานได้ตั้งแต่:

```text
IV4
 ↓
File
 ↓
Watcher
 ↓
Matcher
 ↓
Parser
 ↓
Analysis
 ↓
Database
```

โดยผ่าน End-to-End Mock Test แล้ว

ขั้นต่อไปคือการนำข้อมูลจริงจาก KEYENCE IV4 G500CA เข้ามาทดสอบ เพื่อยืนยันรูปแบบไฟล์และข้อมูลจริง ก่อนพัฒนา Data Analysis, Online Storage และ Dashboard ต่อไป
