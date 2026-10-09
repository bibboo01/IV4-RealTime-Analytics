# Changelog

Version number = `VERSION` file. Show it with `run version`; it also appears in `run monitor`,
`run status`, the Google Sheets Status tab and `logs/health.json`.
Rule: x.y.z - z = bug fix, y = new feature (data and settings stay compatible), x = needs manual steps.

## 1.11.3 - 2026-10-09
- The automatic database check after an abnormal stop is skipped when the database is larger than 1 GB (reading it whole competes with production for an HDD). Run `run doctor-boot` when the line is idle instead.

## 1.11.2 - 2026-10-09
- `run drift` now always prints a table: for every tool the last-2-hours score and NG next to the 7-day normal, and `ok` / `DRIFT` / why it cannot be compared yet (not enough recent or past inspections). Before, a quiet result and a missing baseline looked the same.

## 1.11.1 - 2026-10-09
- Drift check: a tool's NG rate now counts as drifting when it doubles and rises by at least 1 point (was 3 points, too coarse for a line that normally runs at ~0.5% NG).

## 1.11.0 - 2026-10-09
- Start-up self-check after a power cut or crash: the agent writes a marker on a normal stop; at the next start it tells whether the stop was normal or not, how long it was down, how many half-processed files it recovered, and (after an abnormal stop) runs a database integrity check in the background. Saved to `logs\last_boot.json` and the log. Telegram announces an abnormal stop once (a normal restart under 5 minutes stays silent).
- `run doctor-boot`: shows what happened at the last start and checks the database now (`--full` = deeper, slower).

## 1.10.0 - 2026-10-09
- Safe update: `run update <zip> --restart` stops the agent, saves the old program, installs the new one, installs new requirements if `requirements.txt` changed, self-tests the new code in a fresh process (every module imports, settings load), starts the agent and watches it for up to 2 minutes (fresh heartbeat with the new version). If any step fails it restores the old version by itself and starts it again; `.env`, data and credentials are never touched. Without `--restart`, `run update` behaves as before.

## 1.9.0 - 2026-10-09
- Lineage: every inspection now records the program version and a hash of the settings it was processed with (`app_version`, `config_hash`; new columns are added automatically). The agent stores each distinct settings snapshot once (secrets and paths removed). `run lineage` lists the history and what changed between snapshots, so a change in NG% can be traced to a version or setting. Rows saved before 1.9.0 stay empty.
- `run export-dataset <folder>`: copy archived images into a labelled, versioned training set (`images/NG`, `images/OK`, `labels.csv`, `dataset.json` with `ds-<date>-<hash>` version, counts and the lineage). Default = all NG + the same number of random OK images; options `--from/--to`, `--sensor`, `--ok-per-ng`, `--limit`, `--dry-run`. Images are copied, never moved; the same options on the same data give the same version; an existing dataset folder is never overwritten.

## 1.8.0 - 2026-10-09
- Self-healing: `run heal` restarts the agent when it is alive but stuck (heartbeat older than `IV4_HEAL_STALE_SEC`, default 180 s, and the agent has been up longer than that). The 5-minute health-check task now calls it, so a hang fixes itself without anyone watching - `run update` is enough, no reinstall of the tasks. Limits: 3 restarts per hour (then it leaves the problem visible), never starts an agent you stopped with `run stop`. Decisions are logged to `logs\heal.log`; `run heal --dry-run` shows what it would do.

## 1.7.0 - 2026-10-08
- `run report [YYYY-MM-DD]`: one-page daily report as HTML (inspections, yield, NG %, missing files, per sensor, per hour with a bar, worst tools, attention notes). Saved to `reports\report-<date>.html`; open in a browser or Ctrl+P to save as PDF. `--out file.html` chooses the path.

## 1.6.0 - 2026-10-08
- Drift check (early warning): compares each tool's average score and NG rate over the last 2 hours with the previous 7 days, per sensor and program. Shown as an alert in `run monitor`, sent to Telegram, and available on demand with `run drift`. Needs 2,000+ past and `IV4_DRIFT_MIN` (default 200) recent inspections of that tool. `IV4_DRIFT_PCT` (default 10) = % the average score may move before alerting; 0 = off.

## 1.5.1 - 2026-10-08
- GitHub Actions (`.github/workflows/tests.yml`): the test suite runs on every push and pull request, on Windows and Linux with Python 3.11 and 3.12. No change to the program.

## 1.5.0 - 2026-10-08
- `run rollback`: go back to the program version saved by the last `run update` (`--list` shows saved versions, `--dry-run` previews, or pass a specific `backups\code-*.zip`). Never touches `.env`, data, logs or credentials. The current program is saved first, so running it again undoes the rollback.

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
