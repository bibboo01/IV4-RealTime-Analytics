# Changelog

Version number = `VERSION` file. Show it with `run version`; it also appears in `run monitor`,
`run status`, the Google Sheets Status tab and `logs/health.json`.
Rule: x.y.z - z = bug fix, y = new feature (data and settings stay compatible), x = needs manual steps.

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
