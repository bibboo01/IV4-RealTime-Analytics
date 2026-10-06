# Changelog

Version number = `VERSION` file. Show it with `run version`; it also appears in `run monitor`,
`run status`, the Google Sheets Status tab and `logs/health.json`.
Rule: x.y.z - z = bug fix, y = new feature (data and settings stay compatible), x = needs manual steps.

## 1.0.0 - 2026-10-06
First production release.

- Ingest KEYENCE IV4 jpg+txt via FTP folders into SQLite, hourly statistics, per-sensor folders, archive by day/status/hour
- `run monitor` live screen (today, last N hours, per-minute LIVE chart, tools, latest NG, alerts, sensor-clock check)
- Google Drive upload (NG only by default, or ALL) and Google Sheets dashboard; `run upload` diagnosis, `run gdrive-switch`
- `run production` (Windows task/service, firewall, Defender, power), `run stop`, `run clear`, `run backup`
- `run update <zip>` updates the program without git and keeps `.env`, `data`, `credentials`
- Retention waits for pending uploads; `IV4_TIME_OFFSET_HOURS` corrects a wrong sensor clock
- Missing = files the sensor counted but never arrived (counter-reset hours shown as `*`)
