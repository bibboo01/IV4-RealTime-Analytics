"""run clear: deletes data only when stopped + confirmed, keeps config/sign-in/sensor folders."""
import pytest

from app import cli, clear
from app.config import load_settings


@pytest.fixture
def s(tmp_path):
    s = load_settings(base_dir=tmp_path)
    s.database_path.parent.mkdir(parents=True, exist_ok=True)
    s.database_path.write_text("db")
    (s.database_path.parent / (s.database_path.name + "-wal")).write_text("w")
    (s.archive_dir / "2026-10-01" / "OK").mkdir(parents=True)
    (s.archive_dir / "2026-10-01" / "OK" / "a.jpg").write_text("img")
    (s.incoming_dir / "IV4-01").mkdir(parents=True)
    (s.incoming_dir / "IV4-01" / "x.jpg").write_text("img")
    s.log_dir.mkdir(parents=True, exist_ok=True)
    (s.log_dir / "iv4_agent.log").write_text("log")
    (s.log_dir / "agent.lock").write_text("")
    (tmp_path / "credentials").mkdir()
    (tmp_path / "credentials" / "token.json").write_text("t")
    (tmp_path / ".env").write_text("IV4_X=1")
    return s


def no_agent():
    return None


def test_dry_run_deletes_nothing(s, capsys):
    assert clear.run(["--dry-run"], s, no_agent) == 0
    assert s.database_path.exists() and (s.archive_dir / "2026-10-01").exists()
    assert "Would delete" in capsys.readouterr().out


def test_refuses_while_agent_runs(s, capsys):
    assert clear.run(["--yes"], s, lambda: "123") == 1
    assert s.database_path.exists() and "run stop" in capsys.readouterr().out


def test_needs_confirmation(s, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "no")
    assert clear.run([], s, no_agent) == 1
    assert s.database_path.exists()
    monkeypatch.setattr("builtins.input", lambda _: "CLEAR")
    assert clear.run([], s, no_agent) == 0
    assert not s.database_path.exists() and not (s.archive_dir / "2026-10-01").exists()
    assert (s.incoming_dir / "IV4-01" / "x.jpg").exists()          # not asked for


def test_non_terminal_needs_yes(s, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert clear.run([], s, no_agent) == 1 and s.database_path.exists()


def test_db_only_and_images_only(s):
    assert clear.run(["--db-only", "--yes"], s, no_agent) == 0
    assert not s.database_path.exists() and (s.archive_dir / "2026-10-01").exists()
    assert clear.run(["--images-only", "--yes"], s, no_agent) == 0
    assert not (s.archive_dir / "2026-10-01").exists()


def test_incoming_and_logs_keep_folders_and_lock(s):
    assert clear.run(["--incoming", "--logs", "--yes"], s, no_agent) == 0
    assert (s.incoming_dir / "IV4-01").is_dir() and not (s.incoming_dir / "IV4-01" / "x.jpg").exists()
    assert not (s.log_dir / "iv4_agent.log").exists() and (s.log_dir / "agent.lock").exists()


def test_never_empties_program_or_root_folder():
    from pathlib import Path
    from app.config import BASE_DIR
    assert clear._unsafe(BASE_DIR) and clear._unsafe(BASE_DIR.parent) and clear._unsafe(Path("/"))


def test_cli_registered():
    assert "clear" in cli.COMMANDS
