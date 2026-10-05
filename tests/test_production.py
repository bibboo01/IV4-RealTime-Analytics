"""`run production`: the plan (commands, XML) and the executor, without touching any real system settings."""
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from app import cli, production
from app.config import load_settings

NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


@pytest.fixture
def s(tmp_path):
    return load_settings(base_dir=tmp_path, overrides={"IV4_SENSORS": "IV4-01,IV4-02"})


def _plan(s, tmp_path, **kw):
    (tmp_path / "deploy").mkdir(exist_ok=True)
    kw.setdefault("nssm", None)
    return production.plan(s, workdir=tmp_path, python=r"D:\iv4\.venv\Scripts\python.exe", **kw)


def _flat(steps):
    return [c.argv for st in steps for c in st.commands]


def test_firewall_opens_ftp_to_local_network_only(s, tmp_path):
    cmds = _flat(_plan(s, tmp_path, passive="50000-50100"))
    add = next(c for c in cmds if c[:5] == ["netsh", "advfirewall", "firewall", "add", "rule"])
    assert "localport=21,50000-50100" in add and "remoteip=LocalSubnet" in add and "dir=in" in add
    assert not any(" " in a for a in add)                  # no argument that needs quoting
    delete = next(c for c in cmds if "delete" in c)
    assert delete[-1] == add[5]                            # re-running replaces the same rule, no duplicates


def test_defender_excludes_each_data_folder_once(s, tmp_path):
    steps = _plan(s, tmp_path)
    cmd = next(c.argv[-1] for st in steps for c in st.commands if "Add-MpPreference" in c.argv[-1])
    assert str(s.data_dir) in cmd and cmd.count(str(s.data_dir)) == 1        # incoming/archive/db are inside data
    assert not any("Add-MpPreference" in " ".join(c) for c in _flat(_plan(s, tmp_path, defender=False)))


def test_defender_excludes_archive_on_another_drive(tmp_path):
    other = tmp_path / "bigdisk" / "archive"
    s = load_settings(base_dir=tmp_path, overrides={"IV4_ARCHIVE_DIR": str(other)})
    cmd = next(c[-1] for c in _flat(_plan(s, tmp_path)) if "Add-MpPreference" in c[-1])
    assert str(s.data_dir) in cmd and str(other) in cmd


def test_power_settings_never_sleep(s, tmp_path):
    cmds = _flat(_plan(s, tmp_path))
    for key in ("standby-timeout-ac", "hibernate-timeout-ac", "disk-timeout-ac"):
        assert ["powercfg", "/change", key, "0"] in cmds
    assert ["powercfg", "/setactive", "SCHEME_MIN"] in cmds


def test_without_nssm_a_restarting_boot_task_is_planned(s, tmp_path):
    steps = _plan(s, tmp_path)
    boot = next(st for st in steps if "Task Scheduler task IV4DataAgent" in st.title)
    (path, data), = boot.files.items()
    root = ET.fromstring(data.decode("utf-16"))
    assert root.find(".//t:BootTrigger", NS) is not None
    assert root.find(".//t:RestartOnFailure/t:Count", NS).text == "999"
    assert root.find(".//t:ExecutionTimeLimit", NS).text == "PT0S"                # never killed after 72 h
    assert root.find(".//t:UserId", NS).text == "S-1-5-18"                          # SYSTEM
    ex = root.find(".//t:Exec", NS)
    assert ex.find("t:Command", NS).text == r"D:\iv4\.venv\Scripts\python.exe"
    assert ex.find("t:Arguments", NS).text == "-X utf8 -m app"
    assert ex.find("t:WorkingDirectory", NS).text == str(tmp_path)
    assert ["schtasks", "/create", "/tn", "IV4DataAgent", "/xml", str(path), "/f"] in [c.argv for c in boot.commands]


def test_with_nssm_the_existing_service_installer_is_used(s, tmp_path):
    nssm = tmp_path / "nssm.exe"
    nssm.write_text("x")
    steps = _plan(s, tmp_path, nssm=str(nssm))
    svc = next(st for st in steps if "Windows service" in st.title)
    assert svc.commands[0].argv[-3:] == [str(tmp_path / "deploy" / "install_service.ps1"), "-Nssm", str(nssm)]
    assert svc.skip_if.argv == ["sc", "query", "IV4DataAgent"]                       # re-running does not reinstall
    assert not any("Task Scheduler task IV4DataAgent" in st.title for st in steps)


def test_health_and_backup_tasks(s, tmp_path):
    steps = _plan(s, tmp_path)
    health = next(st for st in steps if "Health check" in st.title)
    root = ET.fromstring(next(iter(health.files.values())).decode("utf-16"))
    assert root.find(".//t:Repetition/t:Interval", NS).text == "PT5M"
    assert "healthcheck.ps1" in root.find(".//t:Exec/t:Arguments", NS).text
    backup = next(st for st in steps if st.title.startswith("Database backup"))
    root = ET.fromstring(next(iter(backup.files.values())).decode("utf-16"))
    assert root.find(".//t:ScheduleByDay/t:DaysInterval", NS).text == "1"
    assert "02:00:00" in root.find(".//t:CalendarTrigger/t:StartBoundary", NS).text
    assert root.find(".//t:Exec/t:Arguments", NS).text == "-X utf8 -m scripts.backup_db --keep 30"


def test_xml_escapes_special_characters():
    data = production.task_xml("a & b", "C:\\Program Files\\x.exe", '-File "C:\\a b\\<c>.ps1"', "C:\\w", production.TRIGGER_BOOT)
    root = ET.fromstring(data.decode("utf-16"))
    assert root.find(".//t:Arguments", NS).text == '-File "C:\\a b\\<c>.ps1"'


class Recorder:
    def __init__(self, fail=(), exists=()):
        self.calls, self.fail, self.exists = [], set(fail), set(exists)

    def __call__(self, cmd):
        self.calls.append(cmd.argv)
        key = cmd.argv[0] if cmd.argv[0] != "powershell" else "powershell"
        if cmd.argv[:2] == ["sc", "query"]:
            return (0 if "sc" in self.exists else 1), ""
        return (1, "access denied") if key in self.fail else (0, "")


def test_execute_runs_everything_and_cleans_up_xml(s, tmp_path):
    steps = _plan(s, tmp_path)
    out = []
    rec = Recorder()
    assert production.execute(steps, run=rec, out=out.append) == (0, 0)
    assert any(c[0] == "schtasks" for c in rec.calls)
    assert not list((tmp_path / "deploy").glob("_task_*.xml"))                       # temp XML removed
    assert all("[ OK ]" in line or line.startswith("\n") for line in out if "[" in line and "/" not in line[:4])


def test_failures_are_reported_and_do_not_stop_later_steps(s, tmp_path):
    out = []
    rec = Recorder(fail={"netsh", "powershell"})                                     # firewall = FAIL, defender = WARN
    fails, warns = production.execute(_plan(s, tmp_path), run=rec, out=out.append)
    assert fails == 1 and warns == 1
    text = "\n".join(out)
    assert "[FAIL]" in text and "[WARN]" in text and "ask them to exclude" in text
    assert any(c[0] == "schtasks" for c in rec.calls)                                # later steps still ran


def test_existing_service_is_skipped(s, tmp_path):
    nssm = tmp_path / "nssm.exe"
    nssm.write_text("x")
    out = []
    rec = Recorder(exists={"sc"})
    production.execute(_plan(s, tmp_path, nssm=str(nssm)), run=rec, out=out.append)
    assert "[SKIP] service IV4DataAgent already installed" in "\n".join(out)
    assert not any(c[0] == "powershell" and "install_service.ps1" in " ".join(c) for c in rec.calls)


def test_dry_run_changes_nothing(s, tmp_path):
    out = []
    rec = Recorder()
    assert production.execute(_plan(s, tmp_path), dry_run=True, run=rec, out=out.append) == (0, 0)
    assert rec.calls == [] and not list((tmp_path / "deploy").glob("_task_*.xml"))
    assert any("netsh advfirewall firewall add rule" in line for line in out)


def test_review_flags_what_needs_a_decision(tmp_path):
    s = load_settings(base_dir=tmp_path)
    notes = "\n".join(production.review(s))
    assert "Retention is off" in notes and "IV4_SENSORS is empty" in notes and "Windows Update" in notes
    s2 = load_settings(base_dir=tmp_path, overrides={"IV4_RETENTION_OK_DAYS": "7", "IV4_SENSORS": "A,B"})
    assert "Retention is off" not in "\n".join(production.review(s2))


def test_main_dry_run_via_cli_and_refuses_real_run_off_windows(tmp_path, capsys, monkeypatch):
    s = load_settings(base_dir=tmp_path, overrides={"IV4_SENSORS": "IV4-01,IV4-02", "IV4_MIN_FREE_GB": "0"})
    assert cli.COMMANDS["production"](["--dry-run"], s) == 0
    out = capsys.readouterr().out
    assert "Dry run: nothing was changed" in out and "Firewall" in out and "Still to check" in out
    assert cli.COMMANDS["production"]([], s) == 1                                    # this test box is not Windows
    assert "Windows" in capsys.readouterr().out
    assert isinstance(Path(production.__file__), Path)
