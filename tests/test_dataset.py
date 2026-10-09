"""Lineage stamps + run export-dataset."""
import csv
import json

from sqlalchemy import text

from app import dataset, lineage
from app.config import load_settings
from app.database.repository import DatabaseRepository
from tests.test_metrics import rec, save


def make(tmp_path, n_ng=3, n_ok=10):
    s = load_settings(base_dir=tmp_path)
    s.ensure_dirs()
    repo = DatabaseRepository(s.database_path)
    snap = lineage.snapshot(s)
    repo.set_lineage("9.9.9", snap, lineage.config_hash(snap))
    for i in range(n_ng + n_ok):
        status = "NG" if i < n_ng else "OK"
        r = rec(100 + i, ts=f"2026-10-0{6 + i % 3} 09:10:00", status=status)
        save(repo, r)
    with repo.engine.begin() as c:
        for uid, st, ts in list(c.execute(text("SELECT uid, analysis_status, timestamp FROM inspection"))):
            folder = f"{ts[:10]}/{'NG' if st == 'FAIL' else 'OK'}/09/{uid}"
            (s.archive_dir / folder).mkdir(parents=True)
            (s.archive_dir / folder / "a.jpg").write_bytes(b"jpg" + uid.encode())
            c.execute(text("UPDATE inspection SET folder=:f, image_file='a.jpg' WHERE uid=:u"), {"f": folder, "u": uid})
    return s, repo


def test_lineage_snapshot_has_no_secrets_or_paths_and_is_stamped(tmp_path):
    s = load_settings(base_dir=tmp_path, overrides={"IV4_TELEGRAM_TOKEN": "SECRET123", "IV4_TELEGRAM_CHAT_ID": "999"})
    snap = lineage.snapshot(s)
    assert "SECRET123" not in json.dumps(snap) and "999" not in json.dumps(snap)
    assert "telegram_token" not in snap and "database_path" not in snap and "ng_alert_pct" in snap
    assert lineage.config_hash(snap) == lineage.config_hash(dict(snap))
    s2 = load_settings(base_dir=tmp_path, overrides={"IV4_NG_ALERT_PCT": "30"})
    assert lineage.config_hash(lineage.snapshot(s2)) != lineage.config_hash(snap)

    s, repo = make(tmp_path / "x")
    with repo.engine.connect() as c:
        assert {r[0] for r in c.execute(text("SELECT DISTINCT app_version FROM inspection"))} == {"9.9.9"}
        assert c.execute(text("SELECT COUNT(*) FROM config_history")).scalar() == 1
    repo.set_lineage("9.9.9", lineage.snapshot(s), lineage.config_hash(lineage.snapshot(s)))   # same again: no duplicate
    with repo.engine.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM config_history")).scalar() == 1


def test_export_balances_ok_to_ng_writes_labels_and_version(tmp_path, capsys):
    s, repo = make(tmp_path)
    out = tmp_path / "ds"
    assert dataset.run([str(out)], s) == 0
    rows = list(csv.DictReader(open(out / "labels.csv", encoding="utf-8-sig")))
    assert sum(r["label"] == "NG" for r in rows) == 3 and sum(r["label"] == "OK" for r in rows) == 3
    assert all((out / r["file"]).exists() for r in rows) and rows[0]["app_version"] == "9.9.9"
    meta = json.loads((out / "dataset.json").read_text())
    assert meta["dataset_version"].startswith("ds-") and meta["counts"] == {"NG": 3, "OK": 3, "skipped_missing_image": 0}
    assert dataset.run([str(out)], s) == 1                                         # never mix versions in one folder
    again = tmp_path / "ds2"
    dataset.run([str(again)], s)
    assert json.loads((again / "dataset.json").read_text())["dataset_version"] == meta["dataset_version"]  # reproducible


def test_export_options_dry_run_and_missing_images(tmp_path, capsys):
    s, repo = make(tmp_path)
    assert dataset.run([str(tmp_path / "d"), "--dry-run"], s) == 0 and not (tmp_path / "d").exists()
    dataset.run([str(tmp_path / "all"), "--ok-per-ng", "100", "--to", "2026-10-06"], s)      # only 06-10, every OK
    meta = json.loads((tmp_path / "all" / "dataset.json").read_text())
    assert meta["counts"]["OK"] >= 3 and all(
        r["timestamp"].startswith("2026-10-06") for r in csv.DictReader(open(tmp_path / "all" / "labels.csv", encoding="utf-8-sig")))
    import shutil
    shutil.rmtree(s.archive_dir / "2026-10-06")                                    # retention removed a day
    capsys.readouterr()
    dataset.run([str(tmp_path / "gone"), "--to", "2026-10-06", "--dry-run"], s)
    assert "image already deleted" in capsys.readouterr().out
    assert dataset.run([], s) == 2 and dataset.run(["x", "--from", "bad"], s) == 2 and dataset.run(["x", "--nope"], s) == 2


def test_lineage_command_lists_history_and_changes(tmp_path, capsys):
    s, repo = make(tmp_path)
    changed = dict(lineage.snapshot(s), ng_alert_pct=30.0)
    repo.set_lineage("9.9.10", changed, lineage.config_hash(changed))
    capsys.readouterr()
    assert lineage.run([], s) == 0
    out = capsys.readouterr().out
    assert "v9.9.9" in out and "13 inspections" in out and "v9.9.10" in out and "ng_alert_pct=30.0" in out
