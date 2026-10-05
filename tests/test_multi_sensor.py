"""Two (or more) IV4 sensors, each writing to incoming/<sensor>/ via FTP."""
import shutil

from app.config import load_settings
from app.database.repository import DatabaseRepository
from app.ingestion.watcher import IV4Agent
from app.maintenance import oldest_ok_hours, prune_for_space
from app.metrics import summarize, tool_summary
from tests.conftest import jpeg_bytes

IV4_TXT = (
    "Time and Date\t05/10/2026\t{hh}:{mm}:{ss}\r\nProgram No.\t0\r\nTrigger No.\t{trig}\r\n"
    "TIME[ms]\t36\r\nTotal Status\t{st}\r\nTool01:AI Differentiate\tOK\t100\t\r\n"
    "Tool02:AI Differentiate\t{st}\t{v}\t\r\n"
)


def drop(folder, seq, trig, hh="10", mm="00", ss="00", st="OK", image=None):
    stem = f"{seq:05d}_05102026_{hh}{mm}{ss}"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{stem}.txt").write_text(
        IV4_TXT.format(hh=hh, mm=mm, ss=ss, trig=trig, st=st, v=12 if st == "NG" else 99), newline=""
    )
    (folder / f"{stem}.jpg").write_bytes(image or jpeg_bytes())
    return stem


def settle(agent, clock):
    for _ in range(3):
        agent.scan_once()
        clock.advance(1.1)
    agent.scan_once()


def make(tmp_path, **extra):
    s = load_settings(base_dir=tmp_path, overrides={
        "IV4_SETTLE_SECONDS": "1.0", "IV4_SENSORS": "IV4-01,IV4-02", **extra})
    s.ensure_dirs()
    return s


def test_same_filenames_from_two_sensors_are_kept_apart(tmp_path, clock):
    s = make(tmp_path)
    repo = DatabaseRepository(s.database_path)
    try:
        agent = IV4Agent(s, repo, clock=clock)
        # identical filename, identical second, different counters and results
        a = drop(s.incoming_dir / "IV4-01", 1, trig=301512, st="OK", image=jpeg_bytes((1, 2, 3)))
        b = drop(s.incoming_dir / "IV4-02", 1, trig=88001, st="NG", image=jpeg_bytes((200, 9, 9)))
        assert a == b
        settle(agent, clock)

        rows = repo.list_by_inspection_id(a)
        assert sorted((r.camera_id, r.trigger_no, r.analysis_status) for r in rows) == [
            ("IV4-01", 301512, "PASS"), ("IV4-02", 88001, "FAIL")]
        assert all(r.uid.startswith(r.camera_id + "__") for r in rows)
        assert (s.archive_dir / "2026-10-05" / "NG" / "10").is_dir()
    finally:
        repo.dispose()


def test_missing_is_computed_per_sensor(tmp_path, clock):
    """Counters of two sensors must never be mixed when estimating lost files."""
    s = make(tmp_path)
    repo = DatabaseRepository(s.database_path)
    try:
        agent = IV4Agent(s, repo, clock=clock)
        for i, trig in enumerate([1000, 1001, 1002, 1004]):          # 1003 lost
            drop(s.incoming_dir / "IV4-01", i + 1, trig, ss=f"{i:02d}")
        for i, trig in enumerate([500000, 500001]):                 # complete
            drop(s.incoming_dir / "IV4-02", i + 1, trig, ss=f"{i:02d}", st="NG" if i else "OK")
        settle(agent, clock)

        by_sensor = {r.sensor_id: r for r in summarize(repo, by="day", per_sensor=True)}
        assert by_sensor["IV4-01"].total == 4 and by_sensor["IV4-01"].missing == 1
        assert by_sensor["IV4-02"].total == 2 and by_sensor["IV4-02"].missing == 0
        assert by_sensor["IV4-02"].ng_pct == 50.0

        (combined,) = summarize(repo, by="day")
        assert combined.total == 6 and combined.missing == 1       # not ~499,000
        assert combined.active_hours == 1

        tools = {(t.sensor_id, t.tool_no): t for t in tool_summary(repo, per_sensor=True)}
        assert tools[("IV4-02", 2)].ng_count == 1 and tools[("IV4-01", 2)].ng_count == 0
    finally:
        repo.dispose()


def test_sensor_folders_auto_detected(tmp_path, clock):
    s = load_settings(base_dir=tmp_path, overrides={"IV4_SETTLE_SECONDS": "1.0"})
    s.ensure_dirs()
    (s.incoming_dir / "LINE-A").mkdir()
    assert [n for n, _ in s.sensor_sources()] == ["IV4-01", "LINE-A"]
    repo = DatabaseRepository(s.database_path)
    try:
        agent = IV4Agent(s, repo, clock=clock)
        drop(s.incoming_dir / "LINE-A", 7, 10)
        drop(s.incoming_dir, 8, 20)                                   # root = IV4_SENSOR_ID
        settle(agent, clock)
        assert sorted(r.sensor_id for r in summarize(repo, by="day", per_sensor=True)) == ["IV4-01", "LINE-A"]
    finally:
        repo.dispose()


def test_active_hours_reflect_real_running_time(tmp_path, clock):
    s = make(tmp_path)
    repo = DatabaseRepository(s.database_path)
    try:
        agent = IV4Agent(s, repo, clock=clock)
        for n, hh in enumerate(["08", "09", "13"]):
            drop(s.incoming_dir / "IV4-01", n + 1, 100 + n, hh=hh)
        settle(agent, clock)
        assert summarize(repo, by="day")[0].active_hours == 3
    finally:
        repo.dispose()


# ---------------- disk guard ----------------

def _hour(archive, day, status, hh, n=1):
    d = archive / day / status / hh
    for i in range(n):
        (d / f"u{i}").mkdir(parents=True)
        (d / f"u{i}" / "a.jpg").write_bytes(b"x")


def test_disk_guard_deletes_oldest_ok_hours_only(tmp_path):
    a = tmp_path / "archive"
    _hour(a, "2026-10-01", "OK", "08")
    _hour(a, "2026-10-01", "OK", "09")
    _hour(a, "2026-10-01", "NG", "08")
    _hour(a, "2026-10-02", "OK", "10")
    _hour(a, "2026-10-05", "OK", "11")          # current hour - protected

    free = iter([5.0, 10.0, 30.0])                # GB after each deletion
    removed = prune_for_space(a, min_free_gb=20, target_free_gb=25,
                              free_fn=lambda p: next(free), protect="2026-10-05 11")
    assert removed == ["2026-10-01/OK/08", "2026-10-01/OK/09"]
    assert (a / "2026-10-01" / "NG" / "08").exists()                  # NG never touched
    assert (a / "2026-10-02" / "OK" / "10").exists()
    assert not (a / "2026-10-01" / "OK").exists()                       # empty parent cleaned


def test_disk_guard_noop_when_enough_space(tmp_path):
    a = tmp_path / "archive"
    _hour(a, "2026-10-01", "OK", "08")
    assert prune_for_space(a, 20, 25, free_fn=lambda p: 100.0) == []


def test_disk_guard_never_touches_current_hour_or_ng(tmp_path):
    a = tmp_path / "archive"
    _hour(a, "2026-10-05", "OK", "11")
    _hour(a, "2026-10-05", "NG", "10")
    assert oldest_ok_hours(a, protect="2026-10-05 11") == []
    assert prune_for_space(a, 20, 25, free_fn=lambda p: 1.0, protect="2026-10-05 11") == []
    shutil.rmtree(a)


def test_identical_files_from_two_sensors_are_not_duplicates(tmp_path, clock):
    s = make(tmp_path)
    repo = DatabaseRepository(s.database_path)
    try:
        agent = IV4Agent(s, repo, clock=clock)
        img = jpeg_bytes()
        drop(s.incoming_dir / "IV4-01", 1, 5, image=img)
        drop(s.incoming_dir / "IV4-02", 1, 5, image=img)       # byte-identical
        settle(agent, clock)
        assert repo.count() == 2 and agent.stats["duplicates"] == 0
        drop(s.incoming_dir / "IV4-02", 1, 5, image=img)       # same sensor again -> duplicate
        settle(agent, clock)
        assert repo.count() == 2 and agent.stats["duplicates"] == 1
    finally:
        repo.dispose()
