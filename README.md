ได้ครับ ด้านล่างคือ **README.md ฉบับเต็มแบบ Copy ได้ทั้งหมด** สามารถคัดลอกไปวางใน:

```text
D:\iv4-data-agent\README.md
```

````markdown
# IV4 Data Agent

ระบบสำหรับรับข้อมูล Inspection จาก **KEYENCE IV4 G500CA** แบบอัตโนมัติ โดยรับไฟล์จาก Sensor ได้แก่ Image (`JPG/JPEG`) และข้อมูลผลการตรวจสอบ (`TXT`) จากนั้นทำการจับคู่ข้อมูล, Parse ข้อมูล, วิเคราะห์ผล และบันทึกลงฐานข้อมูล

> **Current Status:** Core Realtime Pipeline ผ่าน End-to-End Test แล้ว  
> **Google Drive Integration:** ยังไม่เปิดใช้งาน

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
````

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
│   ├── pipeline.py
│   │
│   ├── ingestion/
│   │   ├── __init__.py
│   │   ├── watcher.py
│   │   ├── matcher.py
│   │   ├── lifecycle.py
│   │   └── record.py
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
├── tests/
│   ├── test_txt_parser.py
│   ├── test_inspection_record.py
│   ├── test_analyzer.py
│   ├── test_database.py
│   └── test_pipeline.py
│
├── .env
├── .gitignore
├── requirements.txt
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

ปัจจุบัน Google Drive ยังไม่ได้เปิดใช้งานใน Core Pipeline

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
    └── 001.json
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

Inspection หนึ่งรายการสามารถประกอบด้วย:

```text
001.jpg
001.txt
001_result.txt
```

---

# 9. Inspection ID

ระบบใช้ชื่อไฟล์ในการระบุ Inspection ID

ตัวอย่าง:

```text
001.jpg
001.txt
001_result.txt
```

จะถูกจัดกลุ่มเป็น:

```text
Inspection ID = 001
```

อีกตัวอย่าง:

```text
007.jpg
007.txt
007_result.txt
```

จะถูกจัดกลุ่มเป็น:

```text
Inspection ID = 007
```

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

หากไม่พบ Image:

```text
Images : 0
Texts  : 3
STATUS : INVALID
```

---

# 11. File Stability Check

เนื่องจาก IV4 อาจกำลังเขียนไฟล์อยู่ ระบบจะไม่ประมวลผลไฟล์ทันทีที่ตรวจพบ

Watcher จะตรวจสอบขนาดไฟล์ซ้ำจนกว่าจะมั่นใจว่าไฟล์เขียนเสร็จ

Current Configuration:

```text
STABILITY_CHECK_INTERVAL = 0.5 seconds

STABILITY_REQUIRED_CHECKS = 3

STABILITY_TIMEOUT = 30 seconds
```

ตัวอย่าง:

```text
[CHECKING] name=007.jpg size=27999 bytes
[CHECKING] name=007.jpg size=27999 bytes
[CHECKING] name=007.jpg size=27999 bytes
[CHECKING] name=007.jpg size=27999 bytes
[READY] name=007.jpg size=27999 bytes
```

---

# 12. TXT Parser

TXT Parser ทำหน้าที่อ่านข้อมูลจาก TXT และแปลงเป็น Structured Data

ตัวอย่าง:

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

Parser จะสร้างข้อมูล:

```text
inspection_id = '007'
timestamp     = '2026-09-28 15:00:15'
machine_id    = 'MACHINE_01'
camera_id     = 'CAM_01'
result        = 'OK'
score         = 97.8
width         = 120.1
height        = 45.0
```

---

# 13. Result TXT

ระบบรองรับ TXT ที่เก็บข้อมูล Result เพิ่มเติม

ตัวอย่าง:

```text
result=OK
defect_count=0
inspection_time_ms=44
confidence=0.978
```

ข้อมูลเหล่านี้จะถูกรวมกับ Inspection Record

---

# 14. Inspection Record

Inspection Record เป็นข้อมูลกลางที่รวมข้อมูลจาก TXT ทั้งหมดของ Inspection เดียวกัน

ตัวอย่าง:

```json
{
  "inspection_id": "007",
  "timestamp": "2026-09-28 15:00:15",
  "machine_id": "MACHINE_01",
  "camera_id": "CAM_01",
  "result": "OK",
  "score": 97.8,
  "width": 120.1,
  "height": 45.0,
  "defect_count": 0,
  "inspection_time_ms": 44,
  "confidence": 0.978,
  "source_files": [
    "007.txt",
    "007_result.txt"
  ]
}
```

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

ระบบใช้ `inspection_id` เป็นตัวระบุ Inspection

หากพบ Inspection ID เดิม ระบบจะ Update แทนการสร้าง Record ใหม่

ตัวอย่าง:

```text
Inspection ID : 006
Action        : UPDATED
Database ID   : 1
```

ทำให้ข้อมูลไม่เกิด Duplicate

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

Activate Virtual Environment:

```powershell
.venv\Scripts\Activate.ps1
```

จากนั้น Run Watcher:

```powershell
python -m app.ingestion.watcher
```

---

# 23. Watcher Startup

เมื่อระบบเริ่มทำงาน:

```text
============================================================
IV4 Data Agent
============================================================
Watching  : D:\iv4-data-agent\data\incoming
Processing: D:\iv4-data-agent\data\processing
Supported : JPG, JPEG, TXT
Stability : 3 checks × 0.5s
Press Ctrl+C to stop.
============================================================
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

# 25. Test Environment

Project ใช้ Python Virtual Environment:

```text
.venv/
```

Activate:

```powershell
.venv\Scripts\Activate.ps1
```

---

# 26. Install Dependencies

ติดตั้ง Dependencies:

```powershell
python -m pip install -r requirements.txt
```

หรือ Update pip:

```powershell
python -m pip install --upgrade pip
```

---

# 27. Current Dependencies

ปัจจุบันมี Dependency หลัก:

```text
watchdog==6.0.0
```

Dependency อื่น ๆ จะถูกเพิ่มตาม Component ที่พัฒนาเพิ่มเติม

---

# 28. Testing

## 28.1 TXT Parser Test

```powershell
python -m tests.test_txt_parser
```

ตรวจสอบ:

* TXT parsing
* Inspection ID
* Timestamp
* Machine ID
* Camera ID
* Result
* Score
* Width
* Height
* Defect Count
* Inspection Time
* Confidence

---

## 28.2 Inspection Record Test

```powershell
python -m tests.test_inspection_record
```

ตรวจสอบการสร้าง Inspection Record

---

## 28.3 Analysis Test

```powershell
python -m tests.test_analyzer
```

ตรวจสอบ Analysis Engine

---

## 28.4 Database Test

```powershell
python -m tests.test_database
```

ตรวจสอบ:

* Database Connection
* Create
* Update
* Read
* Duplicate Protection
* Count

---

## 28.5 Pipeline Test

```powershell
python -m tests.test_pipeline
```

ตรวจสอบ:

```text
Parser
   ↓
Analysis
   ↓
Database
```

---

# 29. End-to-End Test

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

# 30. Mock Data Example

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

# 31. End-to-End Test Result

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

| Component            | Status     |
| -------------------- | ---------- |
| File Watcher         | PASS       |
| File Stability Check | PASS       |
| File Matcher         | PASS       |
| TXT Parser           | PASS       |
| Inspection Record    | PASS       |
| Analysis Engine      | PASS       |
| SQLite Database      | PASS       |
| Duplicate Protection | PASS       |
| Realtime Pipeline    | PASS       |
| End-to-End Mock Test | PASS       |
| Google Drive         | NOT ACTIVE |
| Real IV4 Data        | PENDING    |

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

# 34. Future Online Storage

Google Drive / Cloud Storage ยังไม่ได้เปิดใช้งานใน Current Core Pipeline

เป้าหมายในอนาคต:

```text
IV4
 │
 ▼
Mini PC
 │
 ├───────────────┐
 │               │
 ▼               ▼
SQLite       Online Storage
 │               │
 │               └── Google Drive
 │
 ▼
Data Analysis
```

Online Storage จะสามารถเก็บ:

```text
Images
TXT
Result TXT
JSON
Analysis Result
```

โดย Storage Layer จะถูกแยกออกจาก Core Processing เพื่อให้สามารถเปลี่ยน Cloud Provider ได้ในอนาคต

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

ระบบมีพื้นที่สำหรับจัดการข้อมูลที่ผิดปกติ:

```text
data/error/
```

ตัวอย่าง Error:

* File ไม่ครบ
* File ไม่สามารถอ่านได้
* TXT Format ไม่ถูกต้อง
* Parser Error
* Analysis Error
* Database Error
* File ไม่ Stable ภายใน Timeout
* Invalid Inspection Group

---

# 39. Logging

ระบบแสดงสถานะการทำงานผ่าน Console และเตรียม `logs/` สำหรับการพัฒนา Logging System

ตัวอย่าง Log:

```text
[NEW FILE]
[CHECKING]
[READY]
[MATCHER]
[WAITING]
[COMPLETE]
[PARSER]
[ANALYSIS]
[DATABASE]
[DONE]
[ERROR]
```

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

# 41. Recommended `.gitignore`

ตัวอย่าง:

```gitignore
# Python
__pycache__/
*.py[cod]
*.pyo

# Virtual Environment
.venv/
venv/

# Environment
.env
.env.*

# Credentials
credentials/
*.json

# Database
*.db
*.sqlite
*.sqlite3

# Runtime data
data/incoming/*
data/processing/*
data/uploaded/*
data/error/*

# Logs
logs/*
*.log

# IDE
.vscode/
.idea/

# OS
.DS_Store
Thumbs.db
```

> หากมีไฟล์ Mock Data ที่ต้องการเก็บใน Git ให้สร้าง folder แยก เช่น `tests/mock_data/`

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
[PLANNED]
```

Google Drive API / Cloud Storage

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
[PLANNED]
```

* Windows Service
* Auto Start
* Health Check
* Error Recovery
* Log Rotation
* Backup
* Monitoring
* Alerting

---

# 44. Current Limitations

ปัจจุบันระบบยังอยู่ในช่วง Development / Prototype

ข้อจำกัด:

1. ยังไม่ได้ทดสอบกับข้อมูลจริงจาก IV4
2. TXT Format จริงจาก IV4 ยังต้องตรวจสอบ
3. Google Drive ยังไม่ได้เชื่อมต่อ
4. Dashboard ยังไม่ได้สร้าง
5. Data Analysis ขั้นสูงยังไม่ได้สร้าง
6. Production Service ยังไม่ได้ตั้งค่า
7. Error Recovery ยังอยู่ระหว่างพัฒนา
8. Multi-camera ยังไม่ได้ทดสอบเต็มรูปแบบ

---

# 45. Next Step

ลำดับงานที่แนะนำ:

```text
1. ได้ข้อมูลจริงจาก IV4
        ↓
2. ตรวจสอบ JPG
        ↓
3. ตรวจสอบ TXT
        ↓
4. ตรวจสอบ Result TXT
        ↓
5. ตรวจสอบ Filename Pattern
        ↓
6. ทดสอบ Realtime Ingestion
        ↓
7. ปรับ Parser ให้ตรงกับข้อมูลจริง
        ↓
8. ปรับ Analysis Rules
        ↓
9. เก็บข้อมูลจริงเข้า SQLite
        ↓
10. เริ่ม Data Analysis
        ↓
11. เชื่อม Online Storage
        ↓
12. Dashboard
        ↓
13. Production Deployment
```

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
│ Google Drive             ⏸️          │
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

```

**หมายเหตุ:** ตอนนี้ README นี้ตั้งสถานะ **Google Drive = ยังไม่เปิดใช้งาน** ตามที่เราตกลงกัน และแยก `Online Storage` ออกจาก Core Pipeline ไว้ก่อน เพื่อให้ภายหลังจะใช้ Google Drive หรือ Cloud Storage ตัวอื่นก็ไม่ต้องรื้อระบบหลักครับ
```
