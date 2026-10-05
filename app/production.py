"""
One command to prepare a Windows Mini PC for production.

    run production --dry-run         show exactly what would be changed (changes nothing)
    run production                   apply (open CMD / PowerShell with "Run as administrator")
    run production --passive 50000-50100   also open FileZilla's passive port range

What it does (every step is repeatable; a failing step is reported and the rest still run):
  1. Pre-flight check (folders, disk, database, FTP port) - stops if something is broken
  2. Windows Firewall: allow FTP (TCP 21 + passive range) from the local network ONLY
  3. Windows Defender: exclude data / archive / database folders (real-time scan of every
     new image is the biggest slowdown on Windows)
  4. Power: High performance plan, never sleep / hibernate / spin down disks
  5. Start at boot: Windows service via NSSM if nssm.exe is found, otherwise a Task Scheduler
     task running as SYSTEM that restarts itself after a crash
  6. Health check every 5 minutes (writes to the Windows Event Log when unhealthy)
  7. Database backup every day at 02:00 (keeps 30)
Then it prints what to review in .env.
"""
from __future__ import annotations

import argparse
import ctypes
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

from app.config import BASE_DIR, Settings

SERVICE = "IV4DataAgent"
HEALTH_TASK = "IV4Healthcheck"
BACKUP_TASK = "IV4Backup"
FIREWALL_RULE = "IV4-FTP"


@dataclass
class Cmd:
    argv: list[str]
    ignore_fail: bool = False          # e.g. "delete rule" when it does not exist yet


@dataclass
class Step:
    title: str
    commands: list[Cmd] = field(default_factory=list)
    files: dict[Path, bytes] = field(default_factory=dict)       # written before the commands run
    warn_only: bool = False            # failure = WARN (needs IT / not critical), not FAIL
    hint: str = ""                     # shown when the step fails
    skip_if: Cmd | None = None         # exit code 0 -> already done, skip
    skip_message: str = ""


# ------------------------------------------------------------------
# Task Scheduler XML
# ------------------------------------------------------------------

def task_xml(description: str, command: str, arguments: str, workdir: str, trigger: str,
             restart_on_failure: bool = False) -> bytes:
    """Task Scheduler definition (UTF-16, as schtasks /xml expects). Runs as SYSTEM, no time limit."""
    settings = [
        "<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>",
        "<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>",
        "<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>",
        "<StartWhenAvailable>true</StartWhenAvailable>",
        "<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>",
        "<Enabled>true</Enabled>",
    ]
    if restart_on_failure:
        settings.append("<RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure>")
    xml = f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>{escape(description)}</Description></RegistrationInfo>
  <Triggers>{trigger}</Triggers>
  <Principals>
    <Principal id="Author"><UserId>S-1-5-18</UserId><RunLevel>HighestAvailable</RunLevel></Principal>
  </Principals>
  <Settings>{''.join(settings)}</Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(command)}</Command>
      <Arguments>{escape(arguments)}</Arguments>
      <WorkingDirectory>{escape(workdir)}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""
    return xml.encode("utf-16")


TRIGGER_BOOT = "<BootTrigger><Enabled>true</Enabled></BootTrigger>"
TRIGGER_EVERY_5_MIN = ('<TimeTrigger><StartBoundary>2026-01-01T00:00:00</StartBoundary><Enabled>true</Enabled>'
                       '<Repetition><Interval>PT5M</Interval></Repetition></TimeTrigger>')
TRIGGER_DAILY_2AM = ('<CalendarTrigger><StartBoundary>2026-01-01T02:00:00</StartBoundary><Enabled>true</Enabled>'
                     '<ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger>')


def _find_nssm(explicit: str | None) -> str | None:
    for cand in (explicit, shutil.which("nssm"), str(BASE_DIR / "deploy" / "nssm.exe"),
                 r"C:\tools\nssm\win64\nssm.exe", r"C:\nssm\win64\nssm.exe"):
        if cand and Path(cand).exists():
            return str(cand)
    return None


# ------------------------------------------------------------------
# plan
# ------------------------------------------------------------------

def _top_level(paths: list[Path]) -> list[Path]:
    """Drop folders that are inside another listed folder."""
    uniq = sorted({Path(p) for p in paths}, key=lambda p: len(p.parts))
    out: list[Path] = []
    for p in uniq:
        if not any(p == q or q in p.parents for q in out):
            out.append(p)
    return out


def plan(s: Settings, passive: str | None = None, nssm: str | None = None, defender: bool = True,
         service: bool = True, workdir: Path = BASE_DIR, python: str | None = None) -> list[Step]:
    python = python or sys.executable
    work = str(workdir)
    steps: list[Step] = []

    ports = "21" + (f",{passive}" if passive else "")
    steps.append(Step(
        f"Firewall: allow FTP (TCP {ports}) from the local network only",
        [Cmd(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={FIREWALL_RULE}"], ignore_fail=True),
         Cmd(["netsh", "advfirewall", "firewall", "add", "rule", f"name={FIREWALL_RULE}", "dir=in",
              "action=allow", "protocol=TCP", f"localport={ports}", "remoteip=LocalSubnet", "profile=any"])],
        hint="needs Administrator; or ask IT to allow inbound TCP " + ports + " from the sensor network"))

    if defender:
        folders = _top_level([s.data_dir, s.incoming_dir, s.processing_dir, s.archive_dir, s.database_path.parent])
        quoted = ",".join("'" + str(p).replace("'", "''") + "'" for p in folders)
        steps.append(Step(
            "Windows Defender: exclude " + ", ".join(str(p) for p in folders),
            [Cmd(["powershell", "-NoProfile", "-NonInteractive", "-Command", f"Add-MpPreference -ExclusionPath {quoted}"])],
            warn_only=True,
            hint="Defender is managed by your IT / another antivirus: ask them to exclude these folders "
                 "(otherwise every new image is scanned, which slows ingestion a lot)"))

    steps.append(Step(
        "Power: High performance, never sleep / hibernate / spin down disks",
        [Cmd(["powercfg", "/setactive", "SCHEME_MIN"], ignore_fail=True),
         Cmd(["powercfg", "/change", "standby-timeout-ac", "0"]),
         Cmd(["powercfg", "/change", "hibernate-timeout-ac", "0"]),
         Cmd(["powercfg", "/change", "disk-timeout-ac", "0"])],
        warn_only=True, hint="set Power & sleep to 'Never' manually in Windows Settings"))

    if service:
        nssm_path = _find_nssm(nssm)
        if nssm_path:
            steps.append(Step(
                f"Start at boot: Windows service {SERVICE} (NSSM: {nssm_path})",
                [Cmd(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                      str(workdir / "deploy" / "install_service.ps1"), "-Nssm", nssm_path])],
                skip_if=Cmd(["sc", "query", SERVICE]), skip_message=f"service {SERVICE} already installed",
                hint="read the message above; or run: run service install -Nssm <path to nssm.exe>"))
        else:
            xml = task_xml("IV4 Data Agent: starts at boot, restarts itself after a crash",
                           python, "-X utf8 -m app", work, TRIGGER_BOOT, restart_on_failure=True)
            f = workdir / "deploy" / "_task_agent.xml"
            steps.append(Step(
                f"Start at boot: Task Scheduler task {SERVICE} (SYSTEM, auto-restart; no nssm.exe found)",
                [Cmd(["schtasks", "/create", "/tn", SERVICE, "/xml", str(f), "/f"]),
                 Cmd(["schtasks", "/run", "/tn", SERVICE], ignore_fail=True)],
                files={f: xml}, hint="needs Administrator"))

    hc = task_xml("IV4 Data Agent health check (Windows Event Log on failure)", "powershell",
                  f'-NoProfile -ExecutionPolicy Bypass -File "{workdir / "deploy" / "healthcheck.ps1"}"',
                  work, TRIGGER_EVERY_5_MIN)
    f = workdir / "deploy" / "_task_health.xml"
    steps.append(Step(f"Health check every 5 minutes (task {HEALTH_TASK})",
                      [Cmd(["schtasks", "/create", "/tn", HEALTH_TASK, "/xml", str(f), "/f"])],
                      files={f: hc}, warn_only=True, hint="needs Administrator"))

    bk = task_xml("IV4 Data Agent database backup (keeps 30)", python, "-X utf8 -m scripts.backup_db --keep 30",
                  work, TRIGGER_DAILY_2AM)
    f = workdir / "deploy" / "_task_backup.xml"
    steps.append(Step(f"Database backup daily at 02:00 (task {BACKUP_TASK})",
                      [Cmd(["schtasks", "/create", "/tn", BACKUP_TASK, "/xml", str(f), "/f"])],
                      files={f: bk}, warn_only=True, hint="needs Administrator"))
    return steps


def review(s: Settings) -> list[str]:
    """Things only a person can decide - shown at the end."""
    notes = []
    if s.retention_ok_days == 0 and s.retention_ng_days == 0:
        notes.append("Retention is off (IV4_RETENTION_OK_DAYS / IV4_RETENTION_NG_DAYS = 0): images are kept until the disk "
                     f"guard deletes the oldest OK images below {s.min_free_gb:.0f} GB free. Set days if you want a fixed policy.")
    if not s.sensors:
        notes.append("IV4_SENSORS is empty: with 2 sensors set IV4_SENSORS=IV4-01,IV4-02 and one FTP folder each.")
    if s.upload_enabled and s.upload_backend == "gdrive":
        notes.append("Google Drive upload is ON: only NG/UNKNOWN images go up by default (IV4_UPLOAD_STATUSES).")
    if s.use_polling is False and str(s.incoming_dir).startswith("\\\\"):
        notes.append("incoming is a network share: set IV4_USE_POLLING=true.")
    notes.append("Windows Update: set Active hours / a restart window outside production hours.")
    notes.append("Copy the backups\\ folder off this PC regularly (another drive or NAS).")
    return notes


# ------------------------------------------------------------------
# execute
# ------------------------------------------------------------------

def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())          # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return False


def _run(cmd: Cmd) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd.argv, capture_output=True, text=True, errors="replace", timeout=300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()


def execute(steps: list[Step], dry_run: bool = False, run=_run, out=print) -> tuple[int, int]:
    """Returns (failures, warnings)."""
    fails = warns = 0
    for i, st in enumerate(steps, 1):
        out(f"\n[{i}/{len(steps)}] {st.title}")
        if dry_run:
            for p in st.files:
                out(f"      write {p}")
            for c in st.commands:
                out("      > " + subprocess.list2cmdline(c.argv))
            continue
        if st.skip_if is not None and run(st.skip_if)[0] == 0:
            out(f"  [SKIP] {st.skip_message}")
            continue
        try:
            for p, data in st.files.items():
                p.write_bytes(data)
        except OSError as exc:
            out(f"  [FAIL] cannot write {exc}")
            fails += 1
            continue
        failed = ""
        for c in st.commands:
            code, text = run(c)
            if code != 0 and not c.ignore_fail:
                failed = f"{subprocess.list2cmdline(c.argv[:3])} ... exit {code}: {text[:300]}"
                break
        for p in st.files:                       # the XML is only needed once schtasks has read it
            try:
                p.unlink()
            except OSError:
                pass
        if not failed:
            out("  [ OK ]")
        elif st.warn_only:
            warns += 1
            out(f"  [WARN] {failed}\n         -> {st.hint}")
        else:
            fails += 1
            out(f"  [FAIL] {failed}\n         -> {st.hint}")
    return fails, warns


def main(args: list[str], s: Settings) -> int:
    ap = argparse.ArgumentParser(prog="run production", description="Prepare this Windows PC for production.")
    ap.add_argument("--dry-run", action="store_true", help="show what would be done, change nothing")
    ap.add_argument("--passive", help="FileZilla passive port range to open too, e.g. 50000-50100")
    ap.add_argument("--nssm", help="path to nssm.exe (default: auto-detect; without it a Task Scheduler task is used)")
    ap.add_argument("--no-defender", action="store_true", help="do not touch Windows Defender")
    ap.add_argument("--no-service", action="store_true", help="do not set up start-at-boot")
    a = ap.parse_args(args)

    if os.name != "nt" and not a.dry_run:
        print("run production changes Windows settings (firewall, power, services). Use --dry-run to preview,\n"
              "or run it on the Windows Mini PC. (Linux: use a systemd unit.)")
        return 1
    if os.name == "nt" and not a.dry_run and not is_admin():
        print("Run this from an Administrator window: right-click Command Prompt / PowerShell -> "
              "'Run as administrator', cd to the project folder, then: run production\n"
              "(Preview without admin: run production --dry-run)")
        return 1

    from app.cli import ERR, preflight
    print("Pre-flight check")
    problems = 0
    for lvl, msg in preflight(s):
        if lvl != "OK  ":
            print(f"  [{lvl}] {msg}")
        problems += lvl == ERR
    if problems and not a.dry_run:
        print("Fix the FAIL items first (details: run check). Nothing was changed.")
        return 1

    steps = plan(s, passive=a.passive, nssm=a.nssm, defender=not a.no_defender, service=not a.no_service)
    fails, warns = execute(steps, dry_run=a.dry_run)

    print("\nStill to check (only you can decide):")
    for n in review(s):
        print("  - " + n)
    if a.dry_run:
        print("\nDry run: nothing was changed. Run without --dry-run (as Administrator) to apply.")
        return 0
    print(f"\nDone: {fails} failed, {warns} warning(s). Next: run status  /  run monitor")
    return 1 if fails else 0
