"""
`run upload` - why isn't anything going to Google Drive?

Checks, in the order things usually go wrong, and prints a verdict per item:
  1. upload switched on in .env?              (IV4_UPLOAD_ENABLED, default OFF)
  2. signed in to Google?                      (credentials/client_secret.json + token.json)
  3. is there anything the filter lets through? (default: only NG/UNKNOWN, never OK)
  4. is the agent running with the current .env?
  5. what did the last upload round say?       (health.json)
Counts come from the database; nothing is uploaded or changed by this command.
"""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import text

from app.config import BASE_DIR, Settings
from app.database.repository import DatabaseRepository

OK, INFO, WARN, FAIL = "OK  ", "INFO", "WARN", "FAIL"


def _hint_for_error(err: str) -> str:
    low = err.lower()
    if "accessnotconfigured" in low or "service_disabled" in low or "has not been used" in low:
        return "Enable 'Google Drive API' in Google Cloud Console (APIs & Services -> Library)."
    if "invalid_grant" in low or "token has been expired or revoked" in low:
        return "The Google sign-in expired or was revoked. Run: run gdrive-switch --test  (same account is fine)."
    if "oauth token not found" in low or "run gdrive-auth" in low:
        return "Not signed in. Run: run gdrive-auth --test"
    if "access_denied" in low or "403" in low:
        return "Google refused access. Publish the OAuth app (or add your Gmail as a Test user), then run gdrive-auth again."
    if "storagequotaexceeded" in low or "quota" in low:
        return "The Google Drive is full (free accounts have 15 GB)."
    if "timed out" in low or "connection" in low or "name resolution" in low or "unreachable" in low:
        return "No internet from this PC (or a proxy/firewall blocks Google). It retries by itself."
    return ""


def _counts(repo: DatabaseRepository) -> dict:
    with repo.engine.connect() as c:
        by_status = {str(k): n for k, n in c.execute(text(
            "SELECT analysis_status, COUNT(*) FROM inspection GROUP BY analysis_status"))}
        uploaded = c.execute(text("SELECT COUNT(*) FROM inspection WHERE uploaded_at IS NOT NULL "
                                  "AND (upload_ref IS NULL OR upload_ref != 'MISSING')")).scalar() or 0
        missing = c.execute(text("SELECT COUNT(*) FROM inspection WHERE upload_ref = 'MISSING'")).scalar() or 0
    return {"by_status": by_status, "uploaded": uploaded, "missing": missing}


def upload_report(s: Settings, repo: DatabaseRepository, health: dict | None, agent_pid: str | None,
                  env_mtime: float | None = None) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    add = out.append
    health = health or {}

    # 1. switched on?
    if not s.upload_enabled:
        add((FAIL, "Upload is OFF: IV4_UPLOAD_ENABLED=false in .env (this is the default). "
                   "Set IV4_UPLOAD_ENABLED=true, then restart the agent."))
    else:
        add((OK, f"Upload is ON (backend {s.upload_backend}, every {s.upload_interval:.0f} s)"))

    # 2. signed in?
    if s.upload_backend == "gdrive":
        if s.gdrive_auth == "oauth":
            if not s.gdrive_credentials or not s.gdrive_credentials.exists():
                add((FAIL, f"Missing {s.gdrive_credentials} - download the OAuth 'Desktop app' client JSON from Google Cloud Console."))
            elif not s.gdrive_token or not s.gdrive_token.exists():
                add((FAIL, "Not signed in to Google (no credentials/token.json). Run: run gdrive-auth --test"))
            else:
                add((OK, "Signed in to Google (credentials/token.json present)"))
        elif not s.gdrive_credentials or not s.gdrive_credentials.exists():
            add((FAIL, f"Service-account file not found: {s.gdrive_credentials}"))

    # 3. does the filter let anything through?
    c = _counts(repo)
    by = c["by_status"]
    total = sum(by.values())
    if s.upload_statuses is None:
        eligible = total
        add((INFO, f"Filter: ALL inspections are uploaded ({total:,} in the database). At full line speed that is "
                   "~190 GB/day - a free Drive (15 GB) fills in minutes."))
    else:
        eligible = sum(n for k, n in by.items() if k in s.upload_statuses)
        names = "+".join(sorted(s.upload_statuses))
        counts = ", ".join(f"{k} {n:,}" for k, n in sorted(by.items())) or "empty"
        add((INFO, f"Filter: only {names} are uploaded (IV4_UPLOAD_STATUSES). Database: {counts}"))
        if total and eligible == 0:
            add((WARN, f"{total:,} inspections recorded but none is {names}, so nothing qualifies yet. "
                       "OK images are never uploaded by default. To send everything: IV4_UPLOAD_STATUSES=ALL "
                       "(only with a big Drive)."))
        elif total == 0:
            add((INFO, "The database is empty - no inspections recorded yet."))
    pending = repo.count_pending_uploads(s.upload_statuses) if eligible else 0
    add((OK if c["uploaded"] else INFO,
         f"Uploaded so far: {c['uploaded']:,}   waiting: {pending:,}"
         + (f"   skipped (images already deleted): {c['missing']:,}" if c["missing"] else "")))
    if c["missing"]:
        add((WARN, "Some inspections were skipped because their images were deleted before upload "
                   "(IV4_RETENTION_*_DAYS too short, or the disk guard ran low on space)."))

    # 4. agent state
    if agent_pid is None:
        add((WARN, "The agent is not running - uploads only happen while it runs (start: run, or the service/task)."))
    else:
        started = health.get("started_at")
        try:
            t0 = datetime.fromisoformat(started).timestamp() if started else None
        except ValueError:
            t0 = None
        if env_mtime and t0 and env_mtime > t0 + 1:
            add((WARN, ".env was changed after the agent started - restart the agent so it reads the new settings."))
        up = health.get("upload") or {}
        if s.upload_enabled and not up.get("enabled"):
            add((WARN, "The running agent started with upload OFF - restart it (it reads .env only at start)."))

    # 5. last upload round
    up = health.get("upload") or {}
    err = up.get("last_upload_error")
    if err:
        hint = _hint_for_error(err)
        add((FAIL, f"Last upload error: {err[:240]}" + (f"\n         -> {hint}" if hint else "")))
    elif up.get("last_upload_at"):
        add((OK, f"Last successful upload: {up['last_upload_at']} (UTC)"))
    elif s.upload_enabled and pending and agent_pid:
        add((WARN, f"{pending:,} waiting but no upload has succeeded yet and no error is reported - "
                   "check logs/iv4_agent.log for [UPLOAD] lines."))
    return out


def run(args: list[str], s: Settings, running_pid) -> int:
    health = None
    try:
        health = json.loads((s.log_dir / "health.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    env = BASE_DIR / ".env"
    repo = DatabaseRepository(s.database_path)
    try:
        report = upload_report(s, repo, health, running_pid(), env.stat().st_mtime if env.exists() else None)
    finally:
        repo.dispose()
    print("Google Drive upload - diagnosis\n")
    for lvl, msg in report:
        print(f"  [{lvl}] {msg}")
    bad = [m for lvl, m in report if lvl == FAIL]
    warned = any(lvl == WARN for lvl, _ in report)
    print("\n" + (f"{len(bad)} problem(s) above stop uploads." if bad
                  else "Nothing blocking, but see the WARN lines." if warned else "No problem found."))
    print("Connection test (uploads a small file): run gdrive-auth --test")
    return 1 if bad else 0


