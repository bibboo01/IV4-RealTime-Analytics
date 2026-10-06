"""Hourly statistics, metrics, retention and disk reporting."""
import json
import shutil
from datetime import date, datetime

from app.analysis.analyzer import analyze_inspection
from app.ingestion.watcher import IV4Agent
from app.maintenance import MaintenanceWorker, purge_archive
from app.metrics import summarize, tool_summary
from app.parser.inspection_record import InspectionRecord


def rec(trigger, ts="2026-10-03 18:19:13", status="OK", tool2=(None, 99), time_ms=36, program=0):
    t2_status = tool2[0] or status
    return InspectionRecord(
        inspection_id=f"{trigger}", timestamp=ts, result=status, trigger_no=trigger,
        inspection_time_ms=time_ms, program_no=program, camera_id="IV4-01", source_format="iv4",
        tools=[
            {"no": 1, "name": "AI Differentiate", "status": "OK", "value": 100},
            {"no": 2, "name": "AI Differentiate", "status": t2_status, "value": tool2[1]},
        ],
        score=min(100, tool2[1]),
    )


def save(repo, r):
    return repo.save_or_update_inspection(r, analyze_inspection(r), uid=f"u{r.trigger_no}")


def test_hourly_counts_yield_and_missing(repo):
    # 10 triggers in hour 18 (301000..301009) but only 8 files received; 2 NG
    for i in [0, 1, 2, 3, 5, 6, 8, 9]:
        ng = i in (3, 8)
        save(repo, rec(301000 + i, status="NG" if ng else "OK", tool2=("NG", 12) if ng else (None, 99),
                       time_ms=40 if i == 9 else 36))
    save(repo, rec(301500, ts="2026-10-03 19:00:01"))

    (h18, h19) = summarize(repo, "2026-10-03", "2026-10-04", by="hour")
    assert (h18.period, h18.total, h18.fail_count, h18.pass_count) == ("2026-10-03 18", 8, 2, 6)
    assert h18.ng_pct == 25.0 and h18.yield_pct == 75.0
    assert h18.expected == 10 and h18.missing == 2
    assert h18.max_time_ms == 40 and h18.avg_time_ms == 36.5
    assert h19.total == 1

    (day,) = summarize(repo, "2026-10-03", "2026-10-04", by="day")
    assert day.total == 9 and day.fail_count == 2 and day.missing == 2


def test_tool_summary(repo):
    save(repo, rec(1, tool2=(None, 97)))
    save(repo, rec(2, tool2=(None, 91)))
    save(repo, rec(3, status="NG", tool2=("NG", 12)))
    t1, t2 = tool_summary(repo)
    assert (t1.ng_count, t1.count, t1.min_ok_value) == (0, 3, 100)
    assert (t2.ng_count, t2.count) == (1, 3)
    assert t2.min_ok_value == 91          # weakest passing value = margin
    assert t2.min_value == 12
    assert round(t2.avg_value, 3) == round((97 + 91 + 12) / 3, 3)


def test_retry_does_not_double_count(repo):
    r = rec(5)
    save(repo, r)
    save(repo, r)                         # same uid -> UPDATED
    assert summarize(repo, by="hour")[0].total == 1


def test_counter_reset_hides_missing_estimate(repo):
    save(repo, rec(500000))
    save(repo, rec(12))                   # sensor restarted
    (h,) = summarize(repo, by="hour")
    assert h.total == 2 and h.expected is None and h.missing is None


def test_program_filter(repo):
    save(repo, rec(1, program=0))
    save(repo, rec(2, program=3))
    assert summarize(repo, by="day", program_no=3)[0].total == 1


def test_iv4_rows_do_not_store_redundant_raw(repo):
    row, _ = save(repo, rec(7))
    assert row.raw_data is None and json.loads(row.tools_json)[1]["value"] == 99


# ---------------- retention ----------------

def _mk(archive, day, status, uid="x"):
    d = archive / day / status / uid
    d.mkdir(parents=True)
    (d / "a.jpg").write_bytes(b"x")


def test_purge_archive_by_status(tmp_path):
    a = tmp_path / "archive"
    today = date(2026, 10, 20)
    _mk(a, "2026-10-01", "OK")
    _mk(a, "2026-10-01", "NG")
    _mk(a, "2026-10-18", "OK")
    (a / "notes").mkdir(parents=True)          # not a date -> never touched
    removed = purge_archive(a, ok_days=7, ng_days=30, today=today)
    assert removed == ["2026-10-01/OK"]
    assert (a / "2026-10-01" / "NG").exists() and (a / "2026-10-18" / "OK").exists()
    assert (a / "notes").exists()

    assert purge_archive(a, ok_days=7, ng_days=0, today=today) == []    # 0 = keep forever
    purge_archive(a, ok_days=1, ng_days=1, today=today)
    assert sorted(p.name for p in a.iterdir()) == ["notes"]             # empty day dirs removed


def test_row_retention_keeps_hourly_stats(settings, repo):
    save(repo, rec(1))
    from sqlalchemy import text
    with repo.engine.begin() as c:
        c.execute(text("UPDATE inspection SET created_at = :d"), {"d": datetime(2020, 1, 1)})
    save(repo, rec(2))
    settings.retention_rows_days = 30
    w = MaintenanceWorker(settings, stop_event=None)
    w.run_once(repo)
    assert repo.count() == 1 and w.stats["removed_rows"] == 1
    assert summarize(repo, by="day")[0].total == 2          # metrics survive


def test_health_reports_disk(settings, repo, clock):
    agent = IV4Agent(settings, repo, clock=clock)
    agent.write_health()
    h = json.loads((settings.log_dir / "health.json").read_text())
    assert h["disk_free_gb"] > 0 and h["min_free_gb"] == 20


def test_low_disk_is_logged(settings, repo, caplog, monkeypatch):
    import app.maintenance as m
    monkeypatch.setattr(m, "disk_free_gb", lambda p: 3.2)
    MaintenanceWorker(settings, stop_event=None).run_once(repo)
    assert "only 3.2 GB free" in caplog.text


def test_metrics_cli(settings, repo, monkeypatch, capsys, tmp_path):
    save(repo, rec(1))
    save(repo, rec(2, status="NG", tool2=("NG", 10)))
    monkeypatch.setattr("scripts.metrics.load_settings", lambda: settings)
    out_csv = tmp_path / "r.csv"
    monkeypatch.setattr("sys.argv", ["m", "--from", "2026-10-03", "--to", "2026-10-04", "--by", "day",
                                     "--csv", str(out_csv)])
    from scripts.metrics import main
    main()
    out = capsys.readouterr().out
    assert "2026-10-03" in out and "50.00" in out and "Tool02" in out
    assert out_csv.read_text(encoding="utf-8-sig").startswith("period,sensor_id,total")


def test_archive_dirs_are_dated(settings, repo, clock):
    from tests.conftest import write_inspection
    agent = IV4Agent(settings, repo, clock=clock)
    write_inspection(settings.incoming_dir, "a", stamp="2026-09-01 08:00:00")
    for _ in range(4):
        agent.scan_once()
        clock.advance(1.1)
    assert (settings.archive_dir / "2026-09-01" / "OK" / "08").is_dir()
    shutil.rmtree(settings.archive_dir)


# ---------------- bulk batch path must equal the row-by-row path ----------------

def _items(recs):
    return [dict(record=r, analysis=analyze_inspection(r), uid=f"u{r.trigger_no}_{i}",
                 content_hash=f"h{r.trigger_no}_{i}") for i, r in enumerate(recs)]


def _snapshot(repo):
    from sqlalchemy import text
    with repo.engine.connect() as c:
        h = c.execute(text("SELECT * FROM hourly_stats ORDER BY 1,2,3")).all()
        t = c.execute(text("SELECT * FROM hourly_tool_stats ORDER BY 1,2,3,4")).all()
    return h, t


def test_batch_metrics_equal_row_by_row(tmp_path):
    from app.database.repository import DatabaseRepository
    recs = []
    trig = 1000
    for i in range(300):
        hh = "08" if i < 150 else "09"
        trig = 5 if i == 200 else trig + (2 if i % 17 == 0 else 1)      # gaps + one counter reset
        ng = i % 13 == 0
        recs.append(rec(trig, ts=f"2026-10-05 {hh}:00:00", status="NG" if ng else "OK",
                        tool2=("NG", 10 + i % 7) if ng else (None, 90 + i % 9), time_ms=30 + i % 11,
                        program=i % 2))
    a = DatabaseRepository(tmp_path / "a.db")
    b = DatabaseRepository(tmp_path / "b.db")
    try:
        for it in _items(recs):
            a.save_or_update_inspection(it["record"], it["analysis"], uid=it["uid"],
                                        content_hash=it["content_hash"])
        items = _items(recs)
        for i in range(0, len(items), 64):                              # uneven batch boundaries
            b.save_batch(items[i:i + 64])
        assert _snapshot(a) == _snapshot(b)
        assert a.count() == b.count() == 300
    finally:
        a.dispose()
        b.dispose()


def test_batch_actions_mixed(repo):
    first = _items([rec(1), rec(2)])
    assert [x[1] for x in repo.save_batch(first)] == ["CREATED", "CREATED"]

    again = _items([rec(1)])                                  # same uid -> UPDATED, not counted twice
    dup_db = [dict(first[1], uid="other")]                    # same hash as stored row -> DUPLICATE
    new = _items([rec(3)])
    dup_batch = [dict(new[0], uid="other2")]                  # same hash as earlier item in this batch
    res = repo.save_batch(again + dup_db + new + dup_batch)
    assert [a for _, a in res] == ["UPDATED", "DUPLICATE", "CREATED", "DUPLICATE"]
    assert res[1][0] == repo.get_by_uid(first[1]["uid"]).id   # points at the stored row
    assert res[3][0] == res[2][0]
    assert repo.count() == 3
    assert summarize(repo, by="day")[0].total == 3


def test_retention_waits_for_pending_upload(tmp_path):
    from datetime import date
    from app.maintenance import purge_archive
    for day in ("2026-10-01", "2026-10-03"):
        (tmp_path / day / "OK" / "10").mkdir(parents=True)
        (tmp_path / day / "OK" / "10" / "a.jpg").write_text("x")
    today = date(2026, 10, 10)
    # 10-01 is 9 days old, 10-03 is 7 days old; keep 2 days; only 10-01 still has pending uploads
    removed = purge_archive(tmp_path, 2, 2, today=today, hold=lambda d, s: d == "2026-10-01")
    assert removed == ["2026-10-03/OK"] and (tmp_path / "2026-10-01").exists()
    # held folders are not kept forever: past keep + 14 days they go anyway
    removed = purge_archive(tmp_path, 2, 2, today=date(2026, 10, 20), hold=lambda d, s: True)
    assert removed == ["2026-10-01/OK"]


def test_count_pending_by_folder_prefix(repo):
    save(repo, rec(1))
    repo.set_folder("u1", "2026-10-03/OK/18/u1")
    assert repo.count_pending_uploads(None, "2026-10-03/OK/") == 1
    assert repo.count_pending_uploads(None, "2026-10-03/NG/") == 0
    repo.mark_uploaded("u1", "ref")
    assert repo.count_pending_uploads(None, "2026-10-03/OK/") == 0
