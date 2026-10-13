# Changelog

Version number = `VERSION` file. Show it with `run version`; it also appears in `run monitor`,
`run status`, the Google Sheets Status tab and `logs/health.json`.
Rule: x.y.z - z = bug fix, y = new feature (data and settings stay compatible), x = needs manual steps.

## 1.17.2 - 2026-10-13
- `run metrics --csv` adds per-tool columns for every period: `tool<N>_ng`, `tool<N>_ng_pct`, `tool<N>_avg_value`, `tool<N>_min_value`, `tool<N>_min_ok_value`, so the CSV shows which tool caused the NG of each hour/day.

## 1.17.1 - 2026-10-13
- `run sensor --sample` CSV now carries everything the sensor sends per piece: program, trigger number, processing time (ms), and status + value of every tool (`tool1_status`, `tool1_value`, ...), plus the archive folder of the image.

## 1.17.0 - 2026-10-13
- Easy image browsing: every archived image now also appears directly in its hour folder, e.g. `archive\2026-10-13\NG\09\091503_T1_00292_13102026_091503.jpg` (time first so it sorts, then the failing tool for NG). Open `NG\<hour>` (or `OK\<hour>`) and see all images without opening one folder per piece. It is a hard link: no extra disk space (Explorer may still count it twice); retention and disk pruning remove it with the hour. On a disk that cannot hard-link, NG images are copied and OK images skipped. Off with `IV4_FLAT_IMAGES=false`.
- New `run images [--day YYYY-MM-DD | --all]` adds them for pieces archived before this version (default today).

## 1.16.0 - 2026-10-13
- New `run sensor`: one command for the sensor's parameters, metrics and accuracy. Prints and saves `reports\sensor-<date>.txt`:
  1. Parameters: `--set "Tool1 threshold 70->65"` writes down a change made on the sensor (the sensor's own settings are not in its files); each change is shown with NG% for 24 h before and after.
  2. Metrics per tool (`--days N`): inspections, NG%, average value, lowest value, lowest value still passed (shows where the pass line sits).
  3. Accuracy: the sensor cannot know if its OK/NG was right, so a person checks a sample. `--sample N` writes a CSV of N random pieces (half sensor-OK, half sensor-NG, with the image name); fill the `truth` column with OK/NG; `--labels file.csv` imports it. Reports the confusion matrix, false-reject and missed-defect rates with 95% ranges, false rejects by tool, and estimated accuracy re-weighted to the real OK/NG totals.
  New tables `sensor_label` and `sensor_note` are added automatically; existing data is untouched.

## 1.15.0 - 2026-10-13
- New email alerts (SMTP, standard library only). Turn on with `IV4_EMAIL_ENABLED=true` and the `IV4_EMAIL_*` settings in `.env`. The agent checks every 30 s and sends ONE mail for everything new: no files for N minutes, files missing this hour, NG above `IV4_NG_ALERT_PCT`, score drift, backlog, low disk, Google Drive/Sheets errors; plus one mail when the PC came back after a power cut or restart. The same alert repeats at most every `IV4_EMAIL_COOLDOWN_MIN` (kept across restarts in `logs\mail_state.json`). A failed send is retried at the next check. The password is only in `.env` and never logged.
- New `run mail` (settings and what is missing) and `run mail test` (sends a test mail and explains login/port/firewall failures). `run check` warns when email is off or incomplete. `logs\health.json` has an `email` block.

## 1.14.0 - 2026-10-13
- New `run quality`: automatic data-quality check from the hourly tables (hours with far fewer inspections, files the sensor counted but never sent, unreadable results, NG spikes versus this line's normal, tools missing from some pieces, scores outside 0-100, slow processing, sensor clock ahead). Prints and saves `reports\quality-<date>.txt`; exit code 1 when something needs a look, so a scheduled task can run it. Option `--days N`.
- New `run export-table`: every inspection of a day as one flat file (Parquet, or gzip CSV with `--format csv`) with `tool<N>_status/value` columns and the program version/settings that produced it, default yesterday, saved to `exports\` (or `--out` a shared drive). Re-runs never overwrite unless `--force`. Parquet needs `pyarrow` (`requirements-export.txt`, installed once on first use; falls back to CSV if it cannot).
- Release pipeline: pushing a tag `vX.Y.Z` (equal to `VERSION`) runs lint + tests, builds the zip and its SHA-256 and publishes a GitHub release. `run update <zip> --sha256=<hash>` refuses a zip that does not match. CI also runs `ruff` (bug-only rules: undefined names, unused imports).

## 1.13.0 - 2026-10-13
- Removed the Telegram integration (`run notify`, shift summaries, Telegram alerts, the power-cut message and the `IV4_TELEGRAM_*`, `IV4_SHIFTS`, `IV4_NOTIFY_*` settings). Alerts are still shown in `run monitor`, `run drift` and `run doctor-boot`. Old `IV4_TELEGRAM_*` lines left in `.env` are ignored and can be deleted. `logs\notify_state.json` is no longer used.

## 1.12.0 - 2026-10-12
- New `run report --excel`: an Excel workbook from the live database with a written summary (findings, low-volume hours, high-NG hours, per-tool NG, drift, next checks), native charts, hourly / daily / per-tool / hour-of-day tables, NG reasons and optional sample NG pictures. Options: `--days N`, `--from/--to`, `--all`, `--images N`, `--lang th|en`, `--out`. Needs `openpyxl` (listed in `requirements-report.txt`, installed automatically on first use).
- Fix: the Telegram "already sent" memory was pruned with the real clock instead of the tick time (only visible when replaying old dates in tests).

## 1.11.4 - 2026-10-09
- Slowdowns now leave evidence: when processing drops below ~4 files/s the agent writes a `[SLOW]` line to `logs\agent.log` (at most every 5 minutes) with the milliseconds per stage and the number of files waiting. The monitor alert now names the slowest step (e.g. `slowest step: db 480 ms/file`).

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
