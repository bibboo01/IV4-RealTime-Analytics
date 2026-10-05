"""
End-to-end tests of the realtime agent (incoming -> processing -> DB / error).
Most use a fake clock and call scan_once() directly so they are fast and
deterministic; one test runs the real watcher loop in a thread.
"""
import threading
import time

from app.ingestion.record import DONE, load_manifest
from app.ingestion.watcher import IV4Agent
from app.upload.queue import process_upload_queue
from tests.conftest import write_inspection


def _scan_until_settled(agent, clock, rounds=3):
    for _ in range(rounds):
        agent.scan_once()
        clock.advance(1.1)
    agent.scan_once()


def _dirs(p):
    return sorted(x.name for x in p.iterdir() if x.is_dir())


def test_complete_inspection_goes_to_db(settings, repo, clock):
    agent = IV4Agent(settings, repo, clock=clock)
    write_inspection(settings.incoming_dir, "007")

    agent.scan_once()                       # first sight: not stable yet
    assert repo.count() == 0
    _scan_until_settled(agent, clock)

    assert repo.count() == 1
    assert list(settings.incoming_dir.iterdir()) == []
    assert _dirs(settings.processing_dir) == []          # in-flight only
    day = settings.archive_dir / "2026-09-28" / "OK" / "15"   # sensor date / status / hour
    (folder,) = _dirs(day)
    assert folder.startswith("007__")
    m = load_manifest(day / folder)
    assert m["status"] == DONE and m["analysis_status"] == "PASS"
    assert m["archive"] == f"2026-09-28/OK/15/{folder}"
    assert repo.get_by_inspection_id("007").folder == m["archive"]
    assert agent.stats["processed"] == 1


def test_waits_for_all_files(settings, repo, clock):
    agent = IV4Agent(settings, repo, clock=clock)
    (settings.incoming_dir).mkdir(exist_ok=True)
    write_inspection(settings.incoming_dir, "008")
    (settings.incoming_dir / "008_result.txt").unlink()
    _scan_until_settled(agent, clock)
    assert repo.count() == 0                 # still WAITING

    (settings.incoming_dir / "008_result.txt").write_text("result=OK\ndefect_count=0\n")
    _scan_until_settled(agent, clock)
    assert repo.count() == 1


def test_counter_reset_does_not_lose_data(settings, repo, clock):
    """Original bug: second '001' (an NG part!) stayed in incoming forever."""
    agent = IV4Agent(settings, repo, clock=clock)
    write_inspection(settings.incoming_dir, "001", result="OK")
    _scan_until_settled(agent, clock)
    write_inspection(settings.incoming_dir, "001", result="NG", stamp="2026-09-29 08:00:00")
    _scan_until_settled(agent, clock)

    assert list(settings.incoming_dir.iterdir()) == []
    rows = repo.list_by_inspection_id("001")
    assert [r.analysis_status for r in rows] == ["PASS", "FAIL"]


def test_backlog_present_at_startup(settings, repo, clock):
    for i in range(5):
        write_inspection(settings.incoming_dir, f"{i:03d}")
    agent = IV4Agent(settings, repo, clock=clock)
    _scan_until_settled(agent, clock)
    assert repo.count() == 5


def test_incomplete_group_times_out_to_error(settings, repo, clock):
    agent = IV4Agent(settings, repo, clock=clock)
    write_inspection(settings.incoming_dir, "009")
    (settings.incoming_dir / "009.txt").unlink()
    (settings.incoming_dir / "009_result.txt").unlink()
    _scan_until_settled(agent, clock)
    assert (settings.incoming_dir / "009.jpg").exists()

    clock.advance(settings.group_timeout)
    agent.scan_once()
    assert list(settings.incoming_dir.iterdir()) == []
    (err,) = _dirs(settings.error_dir)
    assert "INCOMPLETE" in (settings.error_dir / err / "ERROR.txt").read_text()


def test_invalid_group_to_error(settings, repo, clock):
    agent = IV4Agent(settings, repo, clock=clock)
    write_inspection(settings.incoming_dir, "010")
    (settings.incoming_dir / "010.jpeg").write_bytes(b"x")
    _scan_until_settled(agent, clock)
    assert repo.count() == 0
    (err,) = _dirs(settings.error_dir)
    assert "too many images" in (settings.error_dir / err / "ERROR.txt").read_text()


def test_corrupt_image_to_error(settings, repo, clock):
    agent = IV4Agent(settings, repo, clock=clock)
    write_inspection(settings.incoming_dir, "011", image=b"\xff\xd8 not really a jpeg")
    _scan_until_settled(agent, clock)
    assert repo.count() == 0
    (err,) = _dirs(settings.error_dir)
    assert "IMAGE" in (settings.error_dir / err / "ERROR.txt").read_text()
    assert _dirs(settings.processing_dir) == []


def test_identical_redrop_is_flagged_duplicate(settings, repo, clock):
    agent = IV4Agent(settings, repo, clock=clock)
    from tests.conftest import jpeg_bytes

    data = jpeg_bytes()
    write_inspection(settings.incoming_dir, "012", image=data)
    _scan_until_settled(agent, clock)
    write_inspection(settings.incoming_dir, "012", image=data)
    _scan_until_settled(agent, clock)
    assert repo.count() == 1
    assert agent.stats["duplicates"] == 1
    (err,) = _dirs(settings.error_dir)
    assert "DUPLICATE" in (settings.error_dir / err / "ERROR.txt").read_text()


def test_database_outage_is_retried_not_quarantined(settings, repo, clock, monkeypatch):
    agent = IV4Agent(settings, repo, clock=clock)
    real = repo.save_or_update_inspection

    def boom(*a, **k):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(repo, "save_or_update_inspection", boom)
    write_inspection(settings.incoming_dir, "013")
    _scan_until_settled(agent, clock)
    assert repo.count() == 0
    assert _dirs(settings.error_dir) == []          # NOT quarantined
    (folder,) = _dirs(settings.processing_dir)
    assert load_manifest(settings.processing_dir / folder)["status"] == "CLAIMED"

    monkeypatch.setattr(repo, "save_or_update_inspection", real)
    assert agent.recover_processing() == 1         # same path used on restart
    assert repo.count() == 1
    assert _dirs(settings.processing_dir) == []
    assert load_manifest(settings.archive_dir / "2026-09-28" / "OK" / "15" / folder)["status"] == DONE


def test_done_but_not_archived_is_finished_on_recovery(settings, repo, clock, monkeypatch):
    """Crash (or Windows lock) between DB save and the archive move."""
    agent = IV4Agent(settings, repo, clock=clock)
    monkeypatch.setattr(agent, "_archive", lambda *a: None)
    write_inspection(settings.incoming_dir, "015")
    _scan_until_settled(agent, clock)
    (folder,) = _dirs(settings.processing_dir)
    monkeypatch.undo()
    agent.recover_processing()
    assert _dirs(settings.processing_dir) == []
    assert (settings.archive_dir / "2026-09-28" / "OK" / "15" / folder / "015.jpg").exists()


def test_ng_goes_to_ng_folder(settings, repo, clock):
    agent = IV4Agent(settings, repo, clock=clock)
    write_inspection(settings.incoming_dir, "016", result="NG")
    _scan_until_settled(agent, clock)
    assert len(_dirs(settings.archive_dir / "2026-09-28" / "NG" / "15")) == 1


def test_upload_queue_default_sends_only_ng(settings, repo, clock):
    settings.upload_backend = "mock"
    agent = IV4Agent(settings, repo, clock=clock)
    write_inspection(settings.incoming_dir, "014")                 # OK
    write_inspection(settings.incoming_dir, "017", result="NG")    # NG
    _scan_until_settled(agent, clock)
    assert process_upload_queue(settings, repo=repo) == 1
    assert process_upload_queue(settings, repo=repo) == 0           # already uploaded
    assert [d.split("__")[0] for d in _dirs(settings.uploaded_dir)] == ["017"]

    settings.upload_statuses = None                                 # IV4_UPLOAD_STATUSES=ALL
    assert process_upload_queue(settings, repo=repo) == 1
    assert repo.get_by_inspection_id("014").upload_ref == "mock"


def test_health_file(settings, repo, clock):
    agent = IV4Agent(settings, repo, clock=clock)
    agent.write_health()
    assert (settings.log_dir / "health.json").exists()


def test_real_watcher_loop(settings, repo):
    """Run the real loop (watchdog + scanner) and drop a burst of files."""
    settings.settle_seconds = 0.3
    agent = IV4Agent(settings, repo)
    t = threading.Thread(target=agent.run, daemon=True)
    t.start()
    try:
        time.sleep(0.3)
        for i in range(20):
            write_inspection(settings.incoming_dir, f"{i:03d}")
        deadline = time.time() + 15
        while time.time() < deadline and repo.count() < 20:
            time.sleep(0.2)
        assert repo.count() == 20
        assert list(settings.incoming_dir.iterdir()) == []
    finally:
        agent.stop()
        t.join(timeout=10)
    assert not t.is_alive()
