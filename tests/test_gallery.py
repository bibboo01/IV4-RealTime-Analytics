"""Images appear directly in their archive hour folder (hard link), named by time and failing tool."""
import os

from app import gallery
from app.database.repository import DatabaseRepository
from app.ingestion.watcher import IV4Agent
from app.maintenance import prune_for_space, purge_archive
from tests.conftest import write_inspection
from tests.test_agent import _scan_until_settled


def test_flat_name_sorts_by_time_and_names_failing_tool():
    tools = [{"no": 1, "status": "NG"}, {"no": 2, "status": "OK"}]
    assert gallery.flat_name("2026-10-13 09:15:03", tools, "00292 a.jpg", ng=True) == "091503_T1_00292_a.jpg"
    assert gallery.flat_name("2026-10-13 09:15:03", tools, "x.jpg", ng=False) == "091503_x.jpg"
    assert gallery.flat_name(None, None, "x.jpg", ng=True) == "x.jpg"


def test_agent_puts_image_in_hour_folder_without_extra_space(settings, repo, clock):
    agent = IV4Agent(settings, repo, clock=clock)
    write_inspection(settings.incoming_dir, "007")
    _scan_until_settled(agent, clock)
    hour = settings.archive_dir / "2026-09-28" / "OK" / "15"
    flat = [p for p in hour.iterdir() if p.is_file()]
    assert len(flat) == 1 and flat[0].name.endswith("007.jpg") and flat[0].name.startswith("150015_")
    (uid_dir,) = [p for p in hour.iterdir() if p.is_dir()]
    assert os.path.samefile(flat[0], uid_dir / "007.jpg")              # hard link: same file
    # pruning the hour removes both
    prune_for_space(settings.archive_dir, 10, 20, free_fn=lambda _p: 1 if hour.exists() else 99)
    assert not hour.exists()


def test_ng_goes_to_ng_folder_and_retention_still_works(settings, repo, clock):
    agent = IV4Agent(settings, repo, clock=clock)
    write_inspection(settings.incoming_dir, "009", result="NG")
    _scan_until_settled(agent, clock)
    hour = settings.archive_dir / "2026-09-28" / "NG" / "15"
    assert [p for p in hour.iterdir() if p.is_file() and p.suffix == ".jpg"]
    purge_archive(settings.archive_dir, ok_days=1, ng_days=1)
    assert not (settings.archive_dir / "2026-09-28").exists()


def test_off_switch_and_backfill(settings, repo, clock):
    settings.flat_images = False
    agent = IV4Agent(settings, repo, clock=clock)
    write_inspection(settings.incoming_dir, "007")
    _scan_until_settled(agent, clock)
    hour = settings.archive_dir / "2026-09-28" / "OK" / "15"
    assert not [p for p in hour.iterdir() if p.is_file()]
    r = DatabaseRepository(settings.database_path)
    n = gallery.backfill(r, settings.archive_dir, "2026-09-28", "2026-09-28")
    assert n["linked"] == 1
    assert gallery.backfill(r, settings.archive_dir, "2026-09-28", "2026-09-28")["linked"] == 0   # idempotent
    r.dispose()
    assert len([p for p in hour.iterdir() if p.is_file()]) == 1
