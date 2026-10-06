# IV4 Data Agent

ระบบสำหรับรับข้อมูล Inspection จาก **KEYENCE IV4 G500CA** แบบอัตโนมัติ โดยรับไฟล์จาก Sensor ได้แก่ Image (`JPG/JPEG`) และข้อมูลผลการตรวจสอบ (`TXT`) จากนั้นทำการจับคู่ข้อมูล, Parse ข้อมูล, วิเคราะห์ผล และบันทึกลงฐานข้อมูล

> **Current Status:** Core Realtime Pipeline พร้อม Production (hardened, 134 automated tests) — รองรับ sensor หลายตัว, Live monitor ใน terminal, Google Drive + Google Sheets Dashboard, ติดตั้งและรันด้วยคำสั่งเดียว (`run`, `run production`)  
> **เริ่มใช้งาน:** ดูหัวข้อ **ติดตั้งและรัน** ด้านล่าง  
> **Google Drive / Sheets:** พร้อมใช้งาน (ปิดไว้เป็นค่าเริ่มต้น — ตั้ง `IV4_UPLOAD_ENABLED=true` / `IV4_SHEETS_ENABLED=true` ใน `.env` หลังเชื่อมบัญชี)

---

# ติดตั้งและรัน (เริ่มจากศูนย์)

> ใช้เวลาประมาณ 15 นาที ทำบน **Mini PC (Windows 10/11)** ที่ sensor ส่งไฟล์มาหา · Linux/macOS ใช้ `./run.sh` แทน `run` ได้ทุกคำสั่ง

## สิ่งที่ต้องมี

| อะไร | หมายเหตุ |
| --- | --- |
| **Python 3.10 ขึ้นไป** | [python.org](https://www.python.org/downloads/windows/) ตอนติดตั้งต้องติ๊ก *Add python.exe to PATH* |
| **FTP Server (FileZilla Server)** | ให้ sensor ส่งไฟล์เข้ามา (ตั้งค่าในขั้น 3) |
| อินเทอร์เน็ต | ตอนติดตั้งครั้งแรก (โหลด library) และตอนใช้ Google Drive/Sheets เท่านั้น |
| พื้นที่ดิสก์ | ประมาณ 16 GB ต่อชั่วโมงที่เครื่องเดิน (รูป 2 sensor) + DB ~1 GB ต่อวัน — ดูข้อ 14.3 |
| NSSM (ไม่บังคับ) | ถ้าอยากได้ Windows Service จริง ไม่มีก็ใช้ Task Scheduler แทนได้ (ข้อ 44) |

## ขั้นที่ 1 — เอาโปรเจกต์ไปไว้ที่เครื่อง

ก๊อปโฟลเดอร์โปรเจกต์ไปไว้ เช่น `D:\iv4-data-agent` (หรือ `git clone https://github.com/bibboo01/IV4-RealTime-Analytics D:\iv4-data-agent` — ถ้ายังไม่ได้ merge PR เข้า `main` ให้เพิ่ม `-b production-hardening` เพื่อได้โค้ดเวอร์ชันล่าสุด) แล้วเปิด Command Prompt / PowerShell ในโฟลเดอร์นั้น

## ขั้นที่ 2 — รันครั้งแรก (ติดตั้งให้เองทั้งหมด)

```powershell
run
```

(หรือดับเบิลคลิก `run.bat`) ครั้งแรกระบบจะ: สร้าง `.venv` → ติดตั้ง library → สร้างไฟล์ **`.env`** → สร้าง folder ต่าง ๆ → ตรวจความพร้อม → เริ่มทำงาน หยุดด้วย `Ctrl+C` ถ้าขึ้นว่าหา Python ไม่เจอ แปลว่ายังไม่ได้ติ๊ก *Add to PATH* ตอนติดตั้ง Python

## ขั้นที่ 3 — ตั้งค่า

**3.1 ไฟล์ `.env`** — ระบบอ่านค่าจาก **`.env` เท่านั้น** (ไม่อ่าน `.env.example` ซึ่งเป็นแค่แม่แบบ) เปิดด้วย Notepad แล้วแก้ที่สำคัญ (แก้แล้วต้อง restart agent):

| ค่า | ตั้งเป็น |
| --- | --- |
| `IV4_SENSORS` | ชื่อ sensor คั่นด้วยจุลภาค เช่น `IV4-01,IV4-02` (1 sensor = 1 โฟลเดอร์ย่อยใน `incoming`) |
| `IV4_DATE_FORMAT` | `%d/%m/%Y` (วัน/เดือน/ปี) หรือ `%m/%d/%Y` ให้ตรงกับที่ sensor เขียนใน TXT |
| `IV4_INCOMING_DIR` / `IV4_ARCHIVE_DIR` | ปกติไม่ต้องแก้ ถ้ามีดิสก์ใหญ่ให้ชี้ `IV4_ARCHIVE_DIR` ไปที่นั่น (ควรอยู่ไดรฟ์เดียวกับ `incoming`) |
| `IV4_RETENTION_OK_DAYS` / `IV4_RETENTION_NG_DAYS` | จำนวนวันที่เก็บรูป (0 = ไม่ลบเอง ระบบลบรูป OK เก่าสุดเองเมื่อดิสก์เหลือต่ำกว่า `IV4_MIN_FREE_GB`) |
| `IV4_USE_POLLING` | `true` ถ้า `incoming` เป็น network share |
| `IV4_UPLOAD_ENABLED` | `true` เมื่อต้องการอัปโหลดขึ้น Google Drive (ค่าเริ่มต้นปิด) |
| `IV4_SHEETS_ENABLED` | `true` เมื่อต้องการ Google Sheets Dashboard (ค่าเริ่มต้นปิด) |

**3.2 FTP Server (FileZilla) + Sensor** (รายละเอียดข้อ 14.2)
* โหมดโปรโตคอลต้องรองรับ FTP ธรรมดา: *Explicit FTP over TLS **and insecure plain FTP*** (sensor ไม่รองรับ TLS)
* สร้าง user ต่อ sensor (เช่น `iv4_01`, `iv4_02`) แล้ว mount `/` → `D:\iv4-data-agent\data\incoming\IV4-01` (และ `IV4-02`) สิทธิ์ Read + Write
* ใน IV-SmartNavigator ของแต่ละ sensor: ตั้ง FTP ปลายทางเป็น IP ของ Mini PC, user/password ของ sensor นั้น, โฟลเดอร์ปลายทางเป็น `/` (ไม่ใช่ `D:\...`), เลือก Passive mode, และตั้งให้ส่งทุกชิ้น (Good + NG)
* ใน Windows Firewall เปิด TCP 21 และช่วงพอร์ต passive (`run production` ทำให้ในขั้นที่ 6)

## ขั้นที่ 4 — ตรวจความพร้อม

```powershell
run check
```

ต้องขึ้น `READY` (บรรทัด WARN บอกสิ่งที่ควรดู เช่น FileZilla ยังไม่เปิดพอร์ต 21)

## ขั้นที่ 5 — ทดสอบการรับข้อมูล

1. เปิด agent: `run` (ค้างไว้หน้าต่างหนึ่ง)
2. เปิดอีกหน้าต่าง: `run monitor` — ให้ sensor ส่งไฟล์ แล้วดู Speed, ยอดวันนี้ และ Missing (ต้องเป็น 0)
3. ทดสอบโดยไม่ใช้ sensor: ก๊อปไฟล์ตัวอย่างจาก `tests\mock_data\iv4\` ไปวางใน `data\incoming\IV4-01\` แล้วดู `run status`

## ขั้นที่ 6 — ตั้งให้พร้อม production (ทำครั้งเดียว)

เปิด Command Prompt แบบ **Run as administrator** ที่โฟลเดอร์โปรเจกต์:

```powershell
run production --dry-run                  # ดูก่อนว่าจะทำอะไร ไม่แก้อะไร
run production --passive 50000-50100      # ทำจริง (ใส่ช่วงพอร์ต passive ตามที่ตั้งใน FileZilla)
```

จะตั้ง Firewall (เฉพาะวง LAN), ยกเว้น Defender, ไม่ให้เครื่อง sleep, เปิด agent เองตอนบูตและ restart เมื่อ crash, ตรวจสุขภาพทุก 5 นาที และ backup DB ทุกวัน 02:00 (รายละเอียดข้อ 44) หลังจากนี้ไม่ต้องเปิด `run` เอง

## ขั้นที่ 7 — Google Drive / Sheets (ไม่บังคับ)

1. ทำตามข้อ 34 (สร้าง OAuth client แบบ Desktop app, เก็บเป็น `credentials\client_secret.json`, Publish app หรือเพิ่ม Test user)
2. `run gdrive-auth --test` ล็อกอินครั้งเดียว (อย่าปิดหน้าต่างจนขึ้น `Saved token`)
3. แก้ `.env`: `IV4_UPLOAD_ENABLED=true` (อัปโหลดรูป NG) และ/หรือ `IV4_SHEETS_ENABLED=true` (Dashboard — ต้องเปิด Google Sheets API ด้วย, ข้อ 22.2) แล้ว restart agent
4. ไม่ขึ้น? รัน `run upload` ระบบจะบอกสาเหตุและวิธีแก้ · เปลี่ยน Gmail: `run gdrive-switch --test`

## ใช้งานประจำวัน

| อยากรู้/ทำอะไร | คำสั่ง |
| --- | --- |
| ดูสถานะสดใน terminal | `run monitor` |
| สรุปสั้น ๆ (ทำงานอยู่ไหม, ยอดวันนี้) | `run status` |
| หยุดรัน | `run stop` |
| รายงาน/ส่งออก Excel | `run metrics --by day --per-sensor --csv report.csv` |
| Dashboard บน Google Sheets | `run sheets` (หรือเปิดลิงก์ที่ได้) |
| ตรวจว่าทำไมไม่อัปโหลด | `run upload` |
| สำรอง database | `run backup` |

## อัปเดตเวอร์ชัน

1. `run backup` (สำรองก่อน)
2. ดึงโค้ดใหม่ (`git pull` หรือก๊อปทับ) **โดยไม่ลบ** `.env`, `credentials\`, `data\`, `logs\`, `backups\`
3. restart agent (หรือรัน `run` ใหม่) — library ถูกติดตั้งใหม่เฉพาะเมื่อ `requirements.txt` เปลี่ยน และ database อัปเกรดโครงสร้างให้เองโดยข้อมูลเดิมอยู่ครบ

## ถอนการติดตั้ง

หยุด agent แล้วลบ task/service: `schtasks /delete /tn IV4DataAgent /f` (และ `IV4Healthcheck`, `IV4Backup`) หรือ `run service uninstall` ถ้าใช้ NSSM, ลบกฎ firewall `netsh advfirewall firewall delete rule name=IV4-FTP` จากนั้นลบโฟลเดอร์โปรเจกต์ (ถ้าจะเก็บข้อมูลให้ก๊อป `data\` และ `backups\` ไว้ก่อน)

## ปัญหาที่เจอบ่อย

| อาการ | สาเหตุที่เจอบ่อย → วิธีแก้ |
| --- | --- |
| Sensor ขึ้น *Failed to login* | FileZilla บังคับ TLS (เปลี่ยนเป็น *…and insecure plain FTP*), user ไม่มี mount `/` ที่มีสิทธิ์ Read+Write, Firewall ไม่เปิด TCP 21/passive, โฟลเดอร์ปลายทางใน sensor ไม่ใช่ `/` |
| `run check` ขึ้น FAIL | อ่านบรรทัด FAIL: ส่วนใหญ่คือโฟลเดอร์เขียนไม่ได้หรือ `.env` ผิดค่า (ระบบบอกชื่อค่าที่ผิด) |
| `run monitor` ขึ้น STOPPED | agent ไม่ได้ทำงาน → `run` หรือตรวจ Task/Service `IV4DataAgent` |
| `Waiting` เพิ่มขึ้นเรื่อย ๆ | เครื่องรับไม่ทัน → ยกเว้น Defender (`run production`), ใช้ SSD, `run benchmark` (หยุด agent ก่อน) |
| `Missing` ไม่เป็น 0 | sensor ตรวจแล้วแต่ไฟล์ไม่มาถึง → ตรวจ FTP/ความเร็วเครือข่าย/ตั้ง sensor ให้ส่งทุกชิ้น |
| วันที่/เวลาใน metric ผิด | `IV4_DATE_FORMAT` ไม่ตรงกับ sensor (ดู `Time and Date` ใน TXT) |
| ไม่อัปโหลดขึ้น Drive | `run upload` — ที่พบบ่อย: `IV4_UPLOAD_ENABLED` ยังเป็น `false`, มีแต่ชิ้น OK (ส่งเฉพาะ NG เป็นค่าเริ่มต้น), แก้ `.env` แล้วไม่ restart |
| Google ขึ้น 403 access_denied | Publish app หรือเพิ่ม Gmail เป็น Test user ใน OAuth consent screen |
| แก้ `.env.example` แล้วไม่มีผล | ต้องแก้ `.env` (ไฟล์จริง) |

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
│   ├── __main__.py          # python -m app  (= run)
│   ├── cli.py               # คำสั่ง start/check/status/monitor/...
│   ├── config.py            # settings จาก .env / env vars
│   ├── logging_setup.py     # rotating log
│   ├── pipeline.py
│   ├── metrics.py           # metric รายชั่วโมง/วัน (อ่านจากตารางสรุป)
│   ├── monitor.py           # run monitor: หน้าจอสดใน terminal
│   ├── sheets.py            # Google Sheets Dashboard
│   ├── diagnose.py          # run upload: ตรวจว่าทำไมไม่อัปโหลด
│   ├── production.py        # run production: ตั้งเครื่อง Windows ให้พร้อมใช้งานจริง
│   ├── maintenance.py       # retention + disk guard
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
│   ├── install_service.ps1  # Windows Service ด้วย NSSM
│   ├── uninstall_service.ps1
│   └── healthcheck.ps1      # health check (Event Log)
│
├── scripts/
│   ├── backup_db.py
│   ├── metrics.py           # รายงาน metric / export CSV
│   ├── benchmark.py         # วัดว่าเครื่องนี้รองรับได้กี่ sensor
│   ├── bootstrap.py         # ติดตั้งครั้งแรก (venv, library, .env)
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
│   ├── test_google_drive.py
│   ├── test_sheets.py
│   ├── test_production.py
│   ├── test_diagnose.py
│   └── test_cli.py          # + test_metrics / test_multi_sensor / test_database
│
├── run.bat                  # คำสั่งเดียว (Windows)
├── run.sh                   # คำสั่งเดียว (Linux/macOS)
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

## 14.2 Sensor 2 ตัว (หรือมากกว่า)

ตัวนับและชื่อไฟล์ของแต่ละ sensor ซ้ำกันได้ (เช่น ทั้งสองตัวสร้าง `00001_05102026_100000.jpg`)
ถ้าส่งเข้า folder เดียวกันไฟล์จะ **เขียนทับกัน** → **แต่ละ sensor ต้องมี folder ของตัวเอง**

```text
D:\iv4-data-agent\data\incoming\
├── IV4-01\      <- sensor ตัวที่ 1 ส่งมาที่นี่
└── IV4-02\      <- sensor ตัวที่ 2 ส่งมาที่นี่
```

ตั้งค่า (เลือกแบบใดแบบหนึ่ง):

* **FileZilla: user แยกต่อ sensor** (แนะนำ) — user `iv4_01` mount `/` → `...\incoming\IV4-01`, user `iv4_02` → `...\incoming\IV4-02`; ใน IV-SmartNavigator ของแต่ละตัวใส่ user ของตัวเอง โฟลเดอร์ปลายทาง `/`
* **user เดียว** — mount `/` → `...\incoming` แล้วใน IV-SmartNavigator ตั้งโฟลเดอร์ปลายทาง `/IV4-01` กับ `/IV4-02` (โฟลเดอร์ต้องมีอยู่ก่อน)

`.env`: `IV4_SENSORS=IV4-01,IV4-02` (ชื่อ folder = ชื่อ sensor ใน DB/metric)

ผลทดสอบ: 2 sensor × 20 ชิ้น/วินาที, ชื่อไฟล์ซ้ำกันทุกไฟล์ → เข้า DB ครบ 3,002/3,002, แยก sensor ถูก, `missing` = 0 ทั้งคู่

## 14.3 ปริมาณข้อมูล (ชั่วโมงเดินเครื่องไม่แน่นอน)

คิดต่อ **ชั่วโมงที่เครื่องเดินจริง**:

| | 1 sensor | 2 sensors |
| --- | --- | --- |
| รูป | ~8 GB/ชม. | **~16 GB/ชม.** |
| Database | ~35 MB/ชม. | ~70 MB/ชม. |

เนื่องจากชั่วโมงเดินเครื่องแต่ละวันไม่เท่ากัน ระบบจึงจัดการพื้นที่เองด้วย **disk guard**:

* ดิสก์เหลือน้อยกว่า `IV4_MIN_FREE_GB` → ลบ **รูป OK ที่เก่าที่สุด** ทีละชั่วโมง จนกลับมามีพื้นที่ (เช็กทุก 5 นาที)
* **รูป NG ไม่ถูกลบโดย disk guard** — ลบเฉพาะตาม `IV4_RETENTION_NG_DAYS`
* ตัวเลข metric (รายชั่วโมง) ไม่ถูกลบ — ดูย้อนหลังได้เสมอแม้รูปจะถูกลบไปแล้ว
* `run h` ใน `scripts.metrics` = ชั่วโมงที่เครื่องเดินจริงแต่ละวัน — ใช้วางแผน storage จากข้อมูลจริงได้

ตัวอย่าง: ดิสก์ว่าง 1 TB → เก็บรูป OK ได้ ~60 ชม. เดินเครื่อง (2 sensors) ก่อนเริ่มลบอัตโนมัติ

## 14.4 โครงสร้างไฟล์

```text
data/processing/   <- เฉพาะที่กำลังประมวลผล (ปกติว่าง)
data/archive/
└── 2026-10-05/                   (วันที่ตามเวลา sensor)
    ├── OK/10/IV4-01__00001_05102026_100000__<เวลารับ>/   (ชั่วโมง/uid)
    ├── NG/10/IV4-02__.../
    └── UNKNOWN/...
```

Retention ลบทีละ folder วัน/สถานะ — เร็วและไม่กระทบการรับข้อมูล (ทำใน thread แยก)

## 14.5 Metric สำหรับนำเสนอ

ตาราง `hourly_stats` / `hourly_tool_stats` อัปเดตทุกครั้งที่บันทึก → ดึง metric ได้ทันทีไม่ว่าข้อมูลจะมีกี่ล้านแถว

```powershell
run metrics                                                 # วันนี้ รายชั่วโมง
run metrics --from 2026-10-01 --to 2026-10-08 --by day --csv week.csv
run metrics --by day --per-sensor                            # แยกตาม sensor
```

| Metric | ความหมาย |
| --- | --- |
| total / NG / NG % / yield % | นับจากไฟล์ที่ได้รับ |
| avg ms / max ms | Cycle time จาก `TIME[ms]` |
| **missing** | `Trigger No.` ที่ sensor นับ แต่ไม่ได้รับไฟล์ — ควรเป็น 0 ถ้า FTP ส่งครบ (คิดแยกต่อ sensor) |
| **run h** | จำนวนชั่วโมงที่เครื่องเดินจริง |
| NG by tool | Tool ไหนตัด NG เท่าไร, ค่าเฉลี่ย, **lowest OK value** (ระยะห่างก่อนจะเป็น NG) |

> ถ้า `missing` ไม่เป็น 0 แปลว่า sensor/FTP ส่งไม่ทัน — NG % จะคลาดเคลื่อน ต้องแก้ที่การตั้งค่า sensor/เครือข่ายก่อนนำตัวเลขไปใช้

---

## 14.6 ประสิทธิภาพและ Spec เครื่อง

### ความเร็ว (วัดจริงบนเครื่องทดสอบ 2 cores, Linux)

| | ก่อนปรับ | หลังปรับ |
| --- | --- | --- |
| Throughput สูงสุด | 211 ชิ้น/วินาที | **~365–400 ชิ้น/วินาที** |
| Real-time ที่ยืนยันแล้ว | — | **10 sensors (200 ชิ้น/วินาที)** ไม่มีตกหล่น, ตามทันภายใน ~1.5 s |
| RAM | | ~50–70 MB |

สิ่งที่ปรับ: แยกงานไฟล์ (ย้าย/hash/ตรวจรูป/parse/archive) ไปทำใน thread pool, บันทึก DB เป็น batch
(1 transaction ต่อ ~200 ชิ้น, insert แบบ bulk, สรุป metric รายชั่วโมงก่อนเขียน), เลิก fsync manifest
(ถ้าไฟฟ้าดับ ระบบสร้าง manifest ใหม่จากไฟล์เองตอน recovery)

ปรับได้ใน `.env`: `IV4_WORKERS` (0 = อัตโนมัติ), `IV4_BATCH_SIZE` (default 200)

> ตัวเลขข้างบนวัดบน Linux — Windows/NTFS + antivirus จะช้ากว่า **ต้องวัดบน Mini PC จริง**:
>
> ```powershell
> run benchmark --dir D:\iv4-data-agent     # ~2-3 นาที, ไม่แตะข้อมูลจริง
> ```
>
> หยุด service ก่อนวัด และให้ `--dir` อยู่บนดิสก์เดียวกับ `data\`

### Spec ที่แนะนำ

| ส่วน | ขั้นต่ำ (1–2 sensors) | แนะนำ (สูงสุด ~10 sensors) | เหตุผล |
| --- | --- | --- | --- |
| CPU | 4 cores, รุ่นปี 2022+ | 4–8 cores, single-thread แรง | Python ใช้ ~1–2 core เป็นหลัก → ความเร็วต่อ core สำคัญกว่าจำนวน core |
| RAM | 8 GB | 16 GB | Agent ใช้ <100 MB; ที่เหลือสำหรับ Windows, FileZilla, Dashboard |
| ดิสก์ระบบ + DB | SSD NVMe | SSD NVMe | ไฟล์เล็กจำนวนมาก + SQLite — **ห้ามใช้ HDD** สำหรับ incoming/processing/DB |
| ดิสก์เก็บรูป (archive) | ≥ 1 TB SSD | 2–4 TB SSD หรือ NAS | ~8 GB/sensor/ชั่วโมงเดินเครื่อง (ดูข้อ 14.3) |
| Network | Gigabit, แยกวงกับ sensor | Gigabit, การ์ดแลนแยกสำหรับวง sensor | ~19 Mbps/sensor; แยกวงเพื่อไม่ให้ traffic อื่นรบกวน |
| OS | Windows 10/11 Pro | Windows 10/11 Pro / IoT LTSC | ตั้งเวลา update นอกเวลาผลิต |

### ปัจจัยที่มีผลมากที่สุด (เรียงตามความสำคัญ)

1. **Antivirus (Windows Defender)** สแกนทุกไฟล์ใหม่ — ขอ IT ยกเว้น `data\incoming`, `data\processing`,
   `data\archive` และ `data\database` จาก real-time scanning (ทำให้ FTP + agent เร็วขึ้นชัดเจน)
2. **ดิสก์** — SSD สำหรับทุกอย่างที่เขียนบ่อย; archive ย้ายไปดิสก์ใหญ่ได้ด้วย `IV4_ARCHIVE_DIR`
   (ถ้าอยู่คนละ drive การย้ายจะเป็นการ copy — ช้ากว่าแต่ทำใน thread แยก)
3. **Power plan = High performance** และปิด sleep / USB selective suspend
4. **FileZilla Server** อยู่เครื่องเดียวกัน ใช้ CPU ต่อไฟล์ด้วย — benchmark ไม่ได้รวมส่วนนี้ จึงควรเผื่อ ~50%

### ขยายมากกว่า ~10 sensors

ข้อจำกัดคือ Python ประมวลผลหลักใน 1 process — แทนที่จะซื้อเครื่องแรงขึ้น แนะนำ **เพิ่ม Mini PC ตามกลุ่มสาย/โซน**
(เช่น 1 เครื่องต่อ 5–10 sensors) เพราะกระจายความเสี่ยง (เครื่องเดียวเสีย ไม่หยุดทั้งโรงงาน)
และกระจายทั้ง CPU, ดิสก์, network และ FTP แล้วรวมข้อมูลเข้า Dashboard กลาง

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

# 22. Running the System — คำสั่งเดียว

ต้องมี Python 3.10+ ([python.org](https://www.python.org/downloads/windows/) — ติ๊ก *Add python.exe to PATH*) แล้ว:

```powershell
run
```

หรือ **ดับเบิลคลิก `run.bat`** — ครั้งแรกจะ:

1. สร้าง `.venv` และติดตั้ง library (~10–30 วินาที, ต้องต่อเน็ต) — ครั้งต่อไปข้าม (ติดตั้งใหม่เฉพาะเมื่อ `requirements.txt` เปลี่ยน)
2. สร้าง `.env` จาก `.env.example` (ไม่เขียนทับของเดิม)
3. ตรวจความพร้อม (folder, สิทธิ์เขียน, sensor folders, ดิสก์, database, FTP port 21)
4. เริ่มทำงาน — หยุดด้วย `Ctrl+C`

| คำสั่ง | ทำอะไร |
| --- | --- |
| `run` | ติดตั้ง (ถ้ายังไม่ได้) + ตรวจ + เริ่มทำงาน |
| `run check` | ตรวจความพร้อมอย่างเดียว |
| `run stop` | **หยุด agent** อย่างปลอดภัย (รอเขียนข้อมูลที่ค้างให้เสร็จ, หยุด Task/Service `IV4DataAgent` ด้วยเพื่อไม่ให้เปิดกลับเอง) ถ้า 60 วินาทียังไม่หยุด ใช้ `run stop --force` |
| `run status` | กำลังทำงานอยู่ไหม, ไฟล์ค้าง, error ล่าสุด, ยอดวันนี้แยก sensor (exit code 1 = ไม่ทำงาน/ผิดปกติ) |
| `run monitor` | **หน้าจอ monitor สด** ใน terminal อัปเดตทุก 2 วินาที (ดูหัวข้อถัดไป) |
| `run metrics --by day --per-sensor` | รายงาน metric (`run metrics --help`) |
| `run benchmark` | วัดว่าเครื่องนี้รองรับได้กี่ sensor |
| `run gdrive-auth` | ล็อกอิน Google Drive ครั้งเดียว |
| `run upload` | ตรวจว่าทำไมไม่อัปโหลดขึ้น Google Drive (บอกสาเหตุและวิธีแก้) |
| `run gdrive-switch` | เปลี่ยน Gmail: ลืมบัญชีเก่า แล้วล็อกอินใหม่ (หยุด agent ก่อน) |
| `run gdrive-logout` | ลืมบัญชี Google ที่ล็อกอินไว้ |
| `run sheets` | สร้าง/อัปเดต Google Sheets Dashboard หนึ่งครั้ง แล้วแสดงลิงก์ |
| `run backup` | สำรอง database |
| `run test` | รัน automated tests |
| `run production` | **เตรียมเครื่องให้พร้อม production** ครั้งเดียว (Administrator) ดูหัวข้อ 44 · `--dry-run` = ดูก่อนไม่แก้อะไร |
| `run service install` | ติดตั้งเป็น Windows Service (PowerShell แบบ Administrator) |
| `run help` | ดูคำสั่งทั้งหมด |

* เปิดซ้ำไม่ได้: ถ้ามี agent ทำงานอยู่แล้ว (รวมถึง service) จะบอก `Already running` แล้วออก — ไม่เกิดการประมวลผลซ้อน
* ค่าใน `.env` ผิด (เช่น `IV4_GROUP_TIMEOUT=two minutes`) จะบอกชื่อค่าที่ผิดทันที
* Linux/macOS ใช้ `./run.sh` คำสั่งเดียวกัน

## 22.1 Live monitor ใน terminal (`run monitor`)

เปิด terminal อีกหน้าต่าง (หรือผ่าน Remote Desktop) ขณะที่ agent/service ทำงานอยู่ แล้วพิมพ์ `run monitor` — อ่านอย่างเดียว ไม่กระทบการรับข้อมูล ออกด้วย `Ctrl+C`

```
 IV4 Data Agent  -  live monitor                              2026-10-05 09:30:12
 Agent    ● RUNNING  pid 803   heartbeat 1s ago   up 3h 12m
 Speed    40.1 /s last min  (IV4-01 20.0, IV4-02 20.1)  Waiting  IV4-01 3, IV4-02 2
 Session  processed 461,820   PASS 457,102   FAIL 4,718   UNKNOWN 0   dup 0
 Errors   0     Disk free 812 GB

 TODAY 2026-10-05  (sensor clock)
  Sensor          Total       NG     NG %    Yield  Missing  Avg ms  Run h
  IV4-01        230,910    1,155    0.50%   99.50%        0    36.0      4
  IV4-02        230,910    3,563    1.54%   98.46%        0    36.1      4
  ALL           461,820    4,718    1.02%   98.98%        0    36.0      4

 LAST 8 HOURS  (all sensors)          <- กราฟแท่งยอดตรวจ + NG % รายชั่วโมง
 TOOLS TODAY                           <- NG แยก Tool, ค่าเฉลี่ย, ค่าต่ำสุดที่ยังผ่าน (margin)
 LATEST NG                             <- 5 ชิ้น NG ล่าสุด: เวลา, sensor, ชื่อไฟล์, Tool ที่ NG
 ALERTS                                <- agent หยุด/ค้าง, ไฟล์ค้างเยอะ, missing > 0, ดิสก์ใกล้เต็ม, error/upload ล่าสุด
```

| ตัวเลือก | ทำอะไร |
| --- | --- |
| `run monitor --interval 5` | อัปเดตทุก 5 วินาที |
| `run monitor --hours 24` | กราฟรายชั่วโมงย้อนหลัง 24 ชั่วโมง |
| `run monitor --once` | พิมพ์ครั้งเดียวแล้วออก (ใช้ใน script หรือ `> snapshot.txt`) |
| `run monitor --no-color` | ไม่ใช้สี |

* **Speed** = จำนวนชิ้นที่เข้า DB ใน 60 วินาทีล่าสุด แยก sensor — 2 sensor ปกติควรใกล้ 40 /s ตอนเครื่องเดิน
* **Waiting** = ไฟล์ที่รอใน `incoming` — ถ้าเกิน 200 และเพิ่มขึ้นเรื่อย ๆ แปลว่าเครื่องรับไม่ทัน
* **Missing** ต้องเป็น 0 — ถ้าไม่ใช่ แปลว่า sensor ตรวจแล้วแต่ไฟล์ไม่มาถึงทาง FTP
* ทุกตัวเลขอ่านจากตารางสรุปรายชั่วโมง + `logs/health.json` จึงเร็วแม้ DB มีหลายล้านแถว

## 22.2 Google Sheets Dashboard (ดู/นำเสนอจากมือถือหรือเครื่องไหนก็ได้)

agent อัปเดต Google Sheet ชื่อ **IV4 Dashboard** (อยู่ในโฟลเดอร์ IV4 Data Agent บน Drive) ทุก 60 วินาที ส่งเฉพาะ **ตัวเลขสรุป** ไม่ส่งรูปหรือรายชิ้น ทำงานใน thread แยก เน็ตหลุดก็ไม่กระทบการรับไฟล์ (จะส่งใหม่เองเมื่อเน็ตกลับมา)

| แท็บ | เนื้อหา |
| --- | --- |
| Today | ยอดวันนี้แยก sensor: Total, NG, NG %, Yield, Missing, Avg ms, Run h |
| Hourly | 48 ชั่วโมงล่าสุด + กราฟยอดตรวจและ NG % |
| Daily | 30 วันล่าสุด + กราฟ NG % |
| Tools | NG แยกตาม Tool วันนี้ |
| Latest NG | NG 20 ชิ้นล่าสุด |
| Status | heartbeat, ไฟล์ค้าง, error, ดิสก์ |

**ตั้งค่า (ครั้งเดียว)**
1. เชื่อม Google Drive ให้สำเร็จก่อน (`run gdrive-auth --test`, ข้อ 34)
2. ใน Google Cloud Console (โปรเจกต์เดียวกัน) เปิด **APIs & Services → Library → Google Sheets API → Enable**
3. ทดลองสร้าง: `run sheets` จะขึ้น `Dashboard updated: https://docs.google.com/spreadsheets/d/...` เปิดลิงก์นั้นได้เลย
4. ให้อัปเดตเองตลอด: ตั้ง `IV4_SHEETS_ENABLED=true` ใน `.env` แล้ว restart (ปรับรอบได้ที่ `IV4_SHEETS_INTERVAL`, ต่ำสุด 15 วินาที)
5. แชร์: กด Share ใน Google Sheets ตามปกติ

* ใช้สิทธิ์เดิม (`drive.file`) ไม่ต้องล็อกอินใหม่ agent เห็นเฉพาะ Sheet ที่ตัวเองสร้าง
* **เปลี่ยน Gmail:** หยุด agent แล้วรัน `run gdrive-switch --test` (ลบ token และ id โฟลเดอร์/Sheet ของบัญชีเก่า แล้วให้ล็อกอินใหม่ พร้อมแสดงว่าล็อกอินด้วยบัญชีอะไร) ไฟล์ที่อัปโหลดไปแล้วยังอยู่ใน Drive ของบัญชีเก่า ถ้ายังอยู่ในโหมด Testing ต้องเพิ่ม Gmail ใหม่เป็น Test user ก่อน
* ถ้าลบ Sheet ทิ้ง agent จะสร้างใหม่ให้เองรอบถัดไป (ลิงก์เปลี่ยน) ถ้าอยากย้ายหรือเปลี่ยนชื่อไฟล์ ทำได้ตามปกติ ลิงก์ไม่เปลี่ยน
* ดูสถานะที่ `run monitor` บรรทัด `Sheets` ถ้ามี error จะขึ้นใน ALERTS เช่น ยังไม่ได้เปิด Sheets API
* **ตรวจนโยบายบริษัทก่อน** ว่าอนุญาตให้ส่งยอดผลิตขึ้น Google ได้

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
run test          # ติดตั้ง pytest ให้อัตโนมัติครั้งแรก
```

Test ทั้งหมดใช้ temp folder และ temp database — **ไม่แตะ `data/` จริง**

| ไฟล์ | ครอบคลุม |
| --- | --- |
| `tests/test_parsing.py` | TXT parser (UTF-8/UTF-16/cp874), Inspection Record, Analyzer, Matcher |
| `tests/test_database.py` | Create/Update, ID ซ้ำเก็บประวัติ, Duplicate hash, Migration จาก v1 |
| `tests/test_metrics.py` (bulk) | batch insert ให้ผล metric เท่ากับทีละแถวทุกตัว (รวม counter reset), action ผสม CREATED/UPDATED/DUPLICATE |
| `tests/test_cli.py` | คำสั่งเดียว: อ่าน `.env` (comment ท้ายบรรทัด), ค่าผิดบอกชื่อ, กันเปิดซ้ำ, preflight, status, `run monitor`, `gdrive-switch/logout` |
| `tests/test_multi_sensor.py` | 2 sensor ชื่อไฟล์ซ้ำ, missing แยก sensor, auto-detect folder, disk guard ลบเฉพาะ OK เก่าสุด |
| `tests/test_metrics.py` | Metric รายชั่วโมง/วัน, missing จาก Trigger No., NG ตาม Tool, retention, ดิสก์ |
| `tests/test_google_drive.py` | Upload ลงโครงสร้าง วัน/UID, root folder สร้างครั้งเดียว, เน็ตหลุดแล้ว retry ไม่ซ้ำ, config ผิดไม่ทำให้ service ล่ม, ลิงก์ล็อกอินต้องขึ้นแม้เปิด browser ไม่ได้ |
| `tests/test_sheets.py` | Google Sheets: ตัวเลขในแต่ละแท็บ, สร้าง Sheet/แท็บ/กราฟ, สร้างใหม่ถ้าถูกลบ, อัปเดตต่อหลังเน็ตหลุด, request ตรงกับ schema จริงของ Sheets API |
| `tests/test_production.py` | `run production`: คำสั่ง firewall/Defender/power, XML ของ Task Scheduler, ข้ามขั้นที่ทำแล้ว, ขั้นที่พลาดไม่หยุดขั้นถัดไป, dry-run ไม่แก้อะไร |
| `tests/test_diagnose.py` | `run upload`: ปิดอยู่/ยังไม่ล็อกอิน/มีแต่ OK/agent เก่ากว่า `.env`/แปล error ของ Google เป็นวิธีแก้ |
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
run
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
| Live monitor (`run monitor`) | PASS (ทดสอบบน Linux) |
| Google Sheets Dashboard | READY (ปิดไว้จนกว่าจะเชื่อมบัญชี) |
| Production setup (`run production`) | READY (ยังไม่ได้ยืนยันบน Windows จริง) |
| Google Drive Upload    | READY (ปิดไว้จนกว่าจะเชื่อมบัญชี) |
| Real IV4 Data          | PASS (5 ไฟล์จริง, OK เท่านั้น — รอตัวอย่าง NG จริง) |

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
   run gdrive-auth --test
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

**ไม่ขึ้น Google Drive? รัน `run upload`** ตรวจให้ตามลำดับที่พลาดบ่อย: (1) `IV4_UPLOAD_ENABLED` ยังเป็น `false` (ค่าเริ่มต้นคือปิด) (2) ยังไม่ได้ล็อกอิน/ไม่มี `client_secret.json` (3) ค่าเริ่มต้นอัปโหลดเฉพาะ **NG/UNKNOWN** ถ้าที่ผ่านมามีแต่ OK จะไม่มีอะไรขึ้นเลย (ตั้ง `IV4_UPLOAD_STATUSES=ALL` เฉพาะเมื่อ Drive ใหญ่พอ) (4) แก้ `.env` แล้วยังไม่ restart agent (5) error ล่าสุดจาก Google พร้อมวิธีแก้ เช่น ยังไม่ได้เปิด Google Drive API, token หมดอายุ, Drive เต็ม ทดสอบการเชื่อมต่อจริงด้วย `run gdrive-auth --test` สถานะย่อดูได้ที่ `run status` / `run monitor` (บรรทัด Upload)

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
* Backup — `run backup --keep 30`
* Summary — `run monitor` (live) / `run status` / `run metrics`

---

# 44. Production Deployment Checklist (Mini PC)

## ทำครั้งเดียวด้วยคำสั่งเดียว

1. ติดตั้ง Python 3.10+ แล้วก๊อปโปรเจกต์ไปไว้ เช่น `D:\iv4-data-agent` (ขั้นตอนเต็มตั้งแต่ต้นอยู่ที่หัวข้อ **ติดตั้งและรัน** ต้นเอกสาร)
2. ดับเบิลคลิก `run.bat` หนึ่งครั้ง (ติดตั้ง library + สร้าง `.env`) แล้วปิดด้วย `Ctrl+C`
3. แก้ `.env`: `IV4_SENSORS=IV4-01,IV4-02` (และ `IV4_RETENTION_*` ถ้าต้องการนโยบายลบรูป)
4. เปิด Command Prompt แบบ **Run as administrator** ที่โฟลเดอร์โปรเจกต์ แล้วพิมพ์
   ```
   run production --dry-run      <- ดูก่อนว่าจะทำอะไรบ้าง (ไม่แก้อะไร)
   run production --passive 50000-50100      <- ทำจริง (ใส่ช่วงพอร์ต passive ตามที่ตั้งใน FileZilla)
   ```
5. ตรวจผล: `run status` และ `run monitor`

`run production` ทำให้ (ทุกขั้นรันซ้ำได้ ขั้นไหนไม่ผ่านจะแจ้งและทำขั้นถัดไปต่อ):

| ขั้น | ทำอะไร |
| --- | --- |
| Pre-flight | ตรวจ folder, ดิสก์, database ถ้ามี FAIL จะหยุดและไม่แก้อะไร |
| Firewall | เปิดรับ FTP (TCP 21 + passive) **เฉพาะจากวง LAN เดียวกัน** ไม่เปิดสู่อินเทอร์เน็ต |
| Defender | ยกเว้นโฟลเดอร์ data/archive/database (ไม่ให้สแกนทุกไฟล์ที่เข้ามา) ถ้า IT คุมอยู่จะขึ้น WARN ให้ขอ IT ยกเว้นให้ |
| Power | High performance, ไม่ sleep / hibernate / ปิดดิสก์ |
| เปิดเองตอนบูต | Windows Service (ถ้าพบ `nssm.exe`) หรือ Task Scheduler (รันเป็น SYSTEM, restart เองเมื่อ crash) |
| Health check | ทุก 5 นาที เขียนลง Windows Event Log เมื่อผิดปกติ |
| Backup | ทุกวัน 02:00 เก็บ 30 ชุด (`backups\`) |

หลังจากนั้นสิ่งที่ต้องทำเอง: ตั้ง Windows Update ให้ restart นอกเวลาผลิต, ก๊อป `backups\` ออกนอกเครื่องเป็นระยะ, ถ้าใช้ Google ให้ `run gdrive-auth --test` และตั้ง `IV4_UPLOAD_ENABLED` / `IV4_SHEETS_ENABLED`

* ถ้า incoming เป็น network share ตั้ง `IV4_USE_POLLING=true`
* ทดสอบมือ: วางไฟล์ตัวอย่างจาก `tests/mock_data/iv4/` ลง `incoming\IV4-01\` → `run status`
* ทดสอบบนเครื่องจริงก่อนเริ่ม: `run test` (ต้องผ่านทั้งหมด) และ `run benchmark` (หยุด agent ก่อน)
* ถอนการติดตั้ง: `schtasks /delete /tn IV4DataAgent /f` (และ `IV4Healthcheck`, `IV4Backup`) หรือ `run service uninstall` ถ้าใช้ NSSM, ลบกฎ firewall ด้วย `netsh advfirewall firewall delete rule name=IV4-FTP`

---

# 45. Current Limitations / Next Step

ที่ทำแล้ว: รูปแบบ TXT จริง, ส่งทุกชิ้น (Good + NG), 2 sensor แยก folder, retention + disk guard, Live monitor, Google Drive, Google Sheets, `run production`

ที่ยังต้องทำ/ยืนยัน:

1. **ตัวอย่าง NG จริง** — ตอนนี้ทดสอบ NG ด้วยไฟล์ที่สร้างขึ้นเอง (`SYNTHETIC_NG_*`) และยังต้องยืนยันว่าชื่อไฟล์รูปตรงกับ TXT
2. **ตั้ง sensor ให้ส่งทุกชิ้น** แล้วตรวจว่า `Missing` = 0 ใน `run monitor` (ข้อ 14)
3. **Retention** — ระบบรองรับแล้ว (`IV4_RETENTION_*`, disk guard) ต้องกำหนดนโยบายตามพื้นที่ดิสก์จริงและชั่วโมงเดินเครื่อง
4. **Google Drive / Sheets** — ต้องล็อกอินบน Mini PC ครั้งเดียว (ข้อ 34) ไม่ขึ้น → `run upload`
5. **ยังไม่ได้ยืนยันบน Windows จริง:** `run production` และ `run monitor` (ทดสอบแล้วบน Linux) ให้ใช้ `run production --dry-run` ดูก่อน
6. **Dashboard บนเว็บ / Data Analysis เชิงลึก** — ยังไม่ทำ (ตอนนี้มี terminal monitor, Google Sheets และ CSV)

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
│ Real IV4 Data            ✅ (OK) ⏳ NG │
│ Multi-sensor + Full rate ✅          │
│ Live Monitor (terminal)  ✅          │
│ Google Drive             ✅ (รอเชื่อม) │
│ Google Sheets Dashboard  ✅ (รอเชื่อม) │
│ Production Setup         ✅ (รอทดสอบ Win)│
│ Web Dashboard            🔜          │
│ Data Analysis เชิงลึก      🔜          │
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

ตอนนี้ระบบรับข้อมูลจริงจาก KEYENCE IV4 G500CA ได้ครบทุกชิ้นจาก sensor หลายตัว มี Live monitor, Google Sheets Dashboard และตั้งค่าเครื่องให้พร้อมใช้งานจริงด้วยคำสั่งเดียว ขั้นต่อไปคือยืนยันกับไฟล์ NG จริงและการติดตั้งบน Mini PC แล้วจึงต่อยอดเป็น Web Dashboard และ Data Analysis
