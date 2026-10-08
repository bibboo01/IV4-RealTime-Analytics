# Changelog

Version number = `VERSION` file. Show it with `run version`; it also appears in `run monitor`,
`run status`, the Google Sheets Status tab and `logs/health.json`.
Rule: x.y.z - z = bug fix, y = new feature (data and settings stay compatible), x = needs manual steps.

## 1.4.0 - 2026-10-08
- Abnormal NG alert: when this hour's NG share reaches `IV4_NG_ALERT_PCT` (default 15) with at least `IV4_NG_ALERT_MIN` inspections (default 200), `run monitor` shows an alert and Telegram sends one (same cool-down as the other alerts). Set `IV4_NG_ALERT_PCT=0` to turn it off.
- Fewer disk calls per inspection (less load on an HDD): mkdir 5 -> 1, stat 26 -> 16 (measured with strace). Folder layout, manifests, upload and recovery are unchanged.

## 1.3.3 - 2026-10-08
- Removed `IV4_ACTIVE_HOURS` (added in 1.3.2): not needed. If it is still in your `.env`, delete that line.

## 1.3.1 - 2026-10-08
- `run metrics --all`: everything since the first recorded hour (combine with `--by day --csv file.csv` for Excel).

## 1.3.0 - 2026-10-08
- `run restart` (alias `run reboot`): stop the agent (forced after 60 s) and start it again, in one command. Uses the Windows task/service when `run production`/`run service install` set one up. It does not restart the PC.

## 1.2.1 - 2026-10-07
- When the database is locked or busy, the agent now stops after the first failed batch of a pass (retrying at the next pass) instead of waiting for the lock once per queued batch, so one lock no longer freezes the agent for minutes. Nothing is lost: files stay in incoming/processing and are saved when the database is free.

## 1.2.0 - 2026-10-07
- Optional `IV4_RESIZE_OK=640x480`: OK (PASS) images are shrunk when archived, so they take ~3-4x less disk and upload much less. NG/UNKNOWN images keep the original. The new file is checked before it replaces the original; on any problem the original stays. Off by default (`IV4_RESIZE_QUALITY` default 85). This does not reduce the FTP load from the sensor - set that on the IV4 itself.

## 1.1.4 - 2026-10-07
- Telegram: when Telegram cannot be reached, `run notify test|chatid` now says why (DNS, firewall/proxy, HTTPS certificate) and what to ask IT for.

## 1.1.3 - 2026-10-07
- `run monitor` / `run status` open while the agent is busy: opening the database no longer takes a write lock when the schema is already current ("database is locked" at start-up).

## 1.1.2 - 2026-10-07
- Upload marks finished files in one short DB transaction per 20 files instead of one per file, so it clashes less with ingestion ("database is locked").

## 1.1.1 - 2026-10-07
- Speed when data grows: SQLite page cache raised (`IV4_DB_CACHE_MB`, default 128), at most 600 inspections per scan pass (a backlog no longer makes each pass slower), 0.2 s minimum between passes.
- `run monitor` shows `Time/file` (list / move / parse / db / archive in ms) and `capacity ~N files/s`, with an alert when the sensor sends faster than the PC can handle. Same data in `logs/health.json`.

## 1.1.0 - 2026-10-06
- Telegram notifications by work shift (3 shifts of 8 h by default: 08:00-16:00, 16:00-00:00, 00:00-08:00, break optional): shift start, break progress, shift-end summary, and calm alerts while working (no data, 10+ files missing, backlog, disk, upload/Sheets errors; several problems = one message, same alert at most once an hour). `run notify`, `run notify test|chatid|now`. New `.env` settings are optional; nothing to change if you do not use Telegram.

## 1.0.0 - 2026-10-06
First production release.

- Ingest KEYENCE IV4 jpg+txt via FTP folders into SQLite, hourly statistics, per-sensor folders, archive by day/status/hour
- `run monitor` live screen (today, last N hours, per-minute LIVE chart, tools, latest NG, alerts, sensor-clock check)
- Google Drive upload (NG only by default, or ALL) and Google Sheets dashboard; `run upload` diagnosis, `run gdrive-switch`
- `run production` (Windows task/service, firewall, Defender, power), `run stop`, `run clear`, `run backup`
- `run update <zip>` updates the program without git and keeps `.env`, `data`, `credentials`
- Retention waits for pending uploads; `IV4_TIME_OFFSET_HOURS` corrects a wrong sensor clock
- Missing = files the sensor counted but never arrived (counter-reset hours shown as `*`)
