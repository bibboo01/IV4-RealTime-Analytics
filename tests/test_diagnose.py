"""`run upload`: the diagnosis names the real reason nothing reaches Google Drive."""
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from app import cli, diagnose
from app.config import load_settings
from app.database.repository import DatabaseRepository


def _mk(tmp_path, **env):
    overrides = {"IV4_UPLOAD_ENABLED": "true", "IV4_GDRIVE_CREDENTIALS": "credentials/client_secret.json"}
    overrides.update(env)
    s = load_settings(base_dir=tmp_path, overrides=overrides)
    s.ensure_dirs()
    return s


def _signed_in(s):
    s.gdrive_credentials.parent.mkdir(parents=True, exist_ok=True)
    s.gdrive_credentials.write_text("{}")
    s.gdrive_token.write_text("{}")


def _rows(s, ok=0, fail=0, uploaded=0, missing=0):
    repo = DatabaseRepository(s.database_path)
    n = 0
    with repo.engine.begin() as c:
        for status, count, up, ref in (("PASS", ok, None, None), ("FAIL", fail, None, None),
                                        ("FAIL", uploaded, datetime.now(), "folder1"),
                                        ("FAIL", missing, datetime.now(), "MISSING")):
            for _ in range(count):
                n += 1
                c.execute(text("INSERT INTO inspection (uid, inspection_id, analysis_status, folder, uploaded_at, "
                               "upload_ref, created_at, updated_at) VALUES (:u, :i, :s, 'd/x', :up, :ref, :t, :t)"),
                          {"u": f"u{n}", "i": f"i{n}", "s": status, "up": up, "ref": ref,
                           "t": datetime.now(timezone.utc).replace(tzinfo=None)})
    return repo


def _report(s, repo, health=None, pid="123", env_mtime=None):
    return diagnose.upload_report(s, repo, health, pid, env_mtime)


def _text(report):
    return "\n".join(f"[{lvl}] {m}" for lvl, m in report)


def test_off_by_default_is_the_first_finding(tmp_path):
    s = _mk(tmp_path, IV4_UPLOAD_ENABLED="false")
    repo = _rows(s, ok=5)
    rep = _report(s, repo)
    assert rep[0][0] == diagnose.FAIL and "IV4_UPLOAD_ENABLED=false" in rep[0][1] and "restart" in rep[0][1]


def test_not_signed_in(tmp_path):
    s = _mk(tmp_path)
    rep = _text(_report(s, DatabaseRepository(s.database_path)))
    assert "Missing" in rep and "client_secret.json" in rep
    s.gdrive_credentials.parent.mkdir(parents=True, exist_ok=True)
    s.gdrive_credentials.write_text("{}")
    assert "Not signed in to Google" in _text(_report(s, DatabaseRepository(s.database_path)))
    s.gdrive_token.write_text("{}")
    assert "Signed in to Google" in _text(_report(s, DatabaseRepository(s.database_path)))


def test_only_ok_inspections_means_nothing_qualifies(tmp_path):
    s = _mk(tmp_path)
    _signed_in(s)
    repo = _rows(s, ok=500)
    rep = _text(_report(s, repo))
    assert "none is FAIL+UNKNOWN" in rep and "OK images are never uploaded by default" in rep
    assert "IV4_UPLOAD_STATUSES=ALL" in rep


def test_all_filter_and_pending_counts(tmp_path):
    s = _mk(tmp_path, IV4_UPLOAD_STATUSES="ALL")
    _signed_in(s)
    repo = _rows(s, ok=3, fail=2, uploaded=4)
    rep = _text(_report(s, repo))
    assert "ALL inspections are uploaded" in rep and "Uploaded so far: 4" in rep and "waiting: 5" in rep


def test_default_filter_counts_only_ng_as_waiting(tmp_path):
    s = _mk(tmp_path)
    _signed_in(s)
    repo = _rows(s, ok=100, fail=7)
    assert "waiting: 7" in _text(_report(s, repo))


def test_agent_not_running_and_stale_env(tmp_path):
    s = _mk(tmp_path)
    _signed_in(s)
    repo = _rows(s, fail=2)
    assert "agent is not running" in _text(_report(s, repo, pid=None))
    started = datetime(2026, 10, 5, 1, 0, 0, tzinfo=timezone.utc)
    health = {"started_at": started.isoformat(), "upload": {"enabled": True}}
    later = started.timestamp() + 600
    assert ".env was changed after the agent started" in _text(_report(s, repo, health, env_mtime=later))
    assert ".env was changed" not in _text(_report(s, repo, health, env_mtime=started.timestamp() - 600))
    stale = {"started_at": started.isoformat(), "upload": {"enabled": False}}
    assert "started with upload OFF" in _text(_report(s, repo, stale))


@pytest.mark.parametrize("err,expect", [
    ("<HttpError 403 ... SERVICE_DISABLED ... Google Drive API has not been used in project 1>", "Enable 'Google Drive API'"),
    ("invalid_grant: Token has been expired or revoked.", "run gdrive-switch --test"),
    ("OAuth token not found (x). Run once on this PC: run gdrive-auth", "run gdrive-auth --test"),
    ("storageQuotaExceeded", "Drive is full"),
    ("HTTPSConnectionPool: Max retries exceeded, connection refused", "No internet"),
])
def test_last_error_gets_a_plain_hint(tmp_path, err, expect):
    s = _mk(tmp_path)
    _signed_in(s)
    rep = _text(_report(s, _rows(s, fail=1), {"upload": {"enabled": True, "last_upload_error": err}}))
    assert "[FAIL] Last upload error" in rep and expect in rep


def test_skipped_because_images_were_deleted(tmp_path):
    s = _mk(tmp_path)
    _signed_in(s)
    rep = _text(_report(s, _rows(s, missing=3)))
    assert "skipped (images already deleted): 3" in rep and "IV4_RETENTION" in rep


def test_run_command_exit_code_and_output(tmp_path, capsys):
    s = _mk(tmp_path, IV4_UPLOAD_ENABLED="false")
    assert cli.cmd_upload([], s) == 1
    out = capsys.readouterr().out
    assert "diagnosis" in out and "IV4_UPLOAD_ENABLED=false" in out and "problem(s) above stop uploads" in out
    s2 = _mk(tmp_path / "ok", IV4_UPLOAD_BACKEND="mock")
    assert cli.cmd_upload([], s2) == 0


def test_check_and_status_mention_upload_off(tmp_path, capsys):
    s = load_settings(base_dir=tmp_path, overrides={"IV4_MIN_FREE_GB": "0"})
    assert any("upload is OFF" in m for _, m in cli.preflight(s))
    (s.log_dir).mkdir(parents=True, exist_ok=True)
    import json
    (s.log_dir / "health.json").write_text(json.dumps({
        "heartbeat_at": datetime.now(timezone.utc).isoformat(), "processed": 1, "pass": 1, "fail": 0, "unknown": 0,
        "duplicates": 0, "errors": 0, "upload": {"enabled": False}, "incoming_by_sensor": {}}))
    cli.cmd_status([], s)
    assert "Upload     : OFF" in capsys.readouterr().out
