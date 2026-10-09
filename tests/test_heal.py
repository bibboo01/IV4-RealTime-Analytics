"""run heal: restart only an agent that is alive but stuck, never loop, never start a stopped one."""
import json
from datetime import datetime, timedelta, timezone

from app import heal
from app.config import load_settings

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)


def health(beat_age, up_age=3600):
    return {"heartbeat_at": (NOW - timedelta(seconds=beat_age)).isoformat(),
            "started_at": (NOW - timedelta(seconds=up_age)).isoformat()}


def test_decide_cases():
    assert heal.decide(health(5), "42", NOW, [], 180)[0] == "ok"
    assert heal.decide(health(500), "42", NOW, [], 180)[0] == "restart"
    assert heal.decide(health(500), None, NOW, [], 180)[0] == "skip"               # stopped on purpose / crashed
    assert heal.decide(health(500, up_age=60), "42", NOW, [], 180)[0] == "skip"    # just started
    assert heal.decide(None, "42", NOW, [], 180)[0] == "skip"
    three = [NOW - timedelta(minutes=m) for m in (5, 20, 40)]
    act, why = heal.decide(health(500), "42", NOW, three, 180)
    assert act == "skip" and "needs a person" in why
    old = [NOW - timedelta(hours=2)] * 5                                           # old restarts do not count
    assert heal.decide(health(500), "42", NOW, old, 180)[0] == "restart"


def test_run_restarts_once_logs_and_stops_after_three(tmp_path):
    s = load_settings(base_dir=tmp_path)
    s.log_dir.mkdir(parents=True, exist_ok=True)
    (s.log_dir / "health.json").write_text(json.dumps(health(900)))
    calls = []
    for i in range(5):
        heal.run([], s, lambda: "42", restart=lambda: calls.append(1) or 0, now=NOW + timedelta(minutes=i))
    assert len(calls) == 3                                                         # then it stops and waits for a person
    log = (s.log_dir / "heal.log").read_text()
    assert "restart:" in log and "needs a person" in log


def test_run_dry_run_and_healthy_do_nothing(tmp_path):
    s = load_settings(base_dir=tmp_path)
    s.log_dir.mkdir(parents=True, exist_ok=True)
    calls = []
    (s.log_dir / "health.json").write_text(json.dumps(health(900)))
    heal.run(["--dry-run"], s, lambda: "42", restart=lambda: calls.append(1) or 0, now=NOW)
    (s.log_dir / "health.json").write_text(json.dumps(health(3)))
    heal.run([], s, lambda: "42", restart=lambda: calls.append(1) or 0, now=NOW)
    assert calls == [] and not (s.log_dir / "heal.json").exists()
