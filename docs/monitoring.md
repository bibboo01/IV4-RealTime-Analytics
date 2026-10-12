# Grafana + Uptime Kuma (ตัวเลือก ไม่บังคับ)

ระบบหลัก (`run monitor`, `run report --excel`) ใช้งานได้โดยไม่ต้องติดตั้งอะไรเพิ่ม ส่วนนี้เป็นของเสริมสำหรับดูกราฟสดและแจ้งเตือนผ่านช่องทางอื่นที่ไม่ใช่ Telegram

**ข้อควรระวัง** ติดตั้งบนเครื่องอื่น (หรือ SSD) ถ้าทำได้ เพราะ Mini PC ใช้ HDD และกำลังมีปัญหาประมวลผลช้าเป็นช่วงๆ อย่าให้ Grafana แย่งดิสก์กับการเก็บข้อมูล ถ้าต้องลงเครื่องเดียวกัน ตั้งรีเฟรช dashboard เป็น 1 นาทีขึ้นไป (ค่าเริ่มต้น) และ Grafana อ่านเฉพาะตารางสรุปรายชั่วโมง (`hourly_*`) ไม่สแกนตาราง inspection จึงเบามาก DB เปิดแบบอ่านอย่างเดียว (`mode=ro`) ไม่เขียนทับข้อมูล

## 1) Grafana (กราฟสด)
1. ติดตั้ง Grafana OSS (Windows installer) และ plugin: `grafana cli plugins install frser-sqlite-datasource` แล้วรีสตาร์ท Grafana
2. คัดลอก `deploy\grafana\datasource.yaml` ไปที่ `<grafana>\conf\provisioning\datasources\` แก้ path ให้ตรงกับ `data\database\iv4.db`
   - ถ้า Grafana อยู่คนละเครื่อง ให้แชร์โฟลเดอร์ `data\database` แบบอ่านอย่างเดียว หรือคัดลอกสำเนา DB มาเป็นระยะ (SQLite บนเครือข่ายไม่แนะนำให้อ่านสด)
3. คัดลอก `deploy\grafana\dashboards.yaml` ไปที่ `<grafana>\conf\provisioning\dashboards\` แก้ path แล้วรีสตาร์ท Grafana
4. เปิด http://localhost:3000 → Dashboards → "IV4 Data Agent"

มี 6 panel: ยอดตรวจวันนี้, NG% ช่วงที่เลือก, ยอดต่อชั่วโมง, NG% ต่อชั่วโมง, NG% แยกตาม Tool, ตารางไฟล์หาย (ช่องว่างของ counter) แกนเวลาเป็นนาฬิกาของ sensor (ตั้ง timezone เป็น UTC ไว้เพื่อให้แสดงตรงกับเวลาในเครื่องตรวจ)

แก้ dashboard: แก้ `deploy\grafana\build_dashboard.py` แล้วรัน `python deploy\grafana\build_dashboard.py` (เทสต์จะเช็กว่า JSON ตรงกับสคริปต์ และ query ทุกตัวรันได้กับ schema จริง)

## 2) Uptime Kuma (แจ้งเตือนว่า agent ยังทำงานอยู่)
1. ติดตั้ง Uptime Kuma (Docker หรือ Node) บนเครื่องที่เปิดตลอด
2. Add New Monitor → ชนิด **Push** ตั้ง Heartbeat Interval 600 วินาที → คัดลอก Push URL
3. ตั้งตัวแปรระบบ Windows (ไม่ใช่ไฟล์ `.env`) `IV4_KUMA_PUSH_URL=http://<kuma>:3001/api/push/<token>` หรือใส่ `-KumaPushUrl` ในอาร์กิวเมนต์ของงาน Task Scheduler ที่เรียก `deploy\healthcheck.ps1` งานตรวจสุขภาพทุก 5 นาทีจะส่ง up เมื่อปกติ และ down พร้อมสาเหตุเมื่อผิดปกติ
4. ถ้า Mini PC ดับ/ค้างทั้งเครื่อง Kuma จะไม่ได้รับสัญญาณเลยและแจ้งเตือนเอง (จุดนี้ Telegram จากเครื่องเดียวกันทำไม่ได้)
5. ตั้ง Notification เป็นอีเมล/Webhook/LINE ตามที่บริษัทอนุญาต

URL นี้เป็นความลับ อย่าส่งในแชตหรือ commit ลง git
