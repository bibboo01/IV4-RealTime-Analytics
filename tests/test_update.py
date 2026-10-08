"""run update: replaces program files from a zip, never touches data/.env/credentials."""
import zipfile

import pytest

from app import update
from app.config import load_settings


def make_install(root, version):
    (root / "app").mkdir(parents=True)
    (root / "app" / "cli.py").write_text(f"v{version}")
    (root / "app" / "old_module.py").write_text("old") if version == 1 else None
    (root / "scripts").mkdir()
    (root / "scripts" / "x.py").write_text(f"s{version}")
    (root / "run.bat").write_text(f"bat{version}")
    (root / "README.md").write_text(f"readme{version}")


@pytest.fixture
def setup(tmp_path):
    inst = tmp_path / "inst"
    make_install(inst, 1)
    (inst / ".env").write_text("SECRET=1")
    (inst / "data").mkdir()
    (inst / "data" / "iv4.db").write_text("db")
    (inst / "credentials").mkdir()
    (inst / "credentials" / "token.json").write_text("tok")
    new = tmp_path / "new" / "IV4-RealTime-Analytics-main"
    make_install(new, 2)
    (new / ".env").write_text("SECRET=FROM_ZIP_MUST_NOT_COPY")          # even if a zip contains these
    (new / "data").mkdir()
    (new / "data" / "iv4.db").write_text("zipdb")
    zpath = tmp_path / "v2.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        for p in (tmp_path / "new").rglob("*"):
            if p.is_file():
                z.write(p, p.relative_to(tmp_path / "new").as_posix())
    s = load_settings(base_dir=inst)
    return inst, zpath, s


def test_update_replaces_code_keeps_data(setup, capsys):
    inst, zpath, s = setup
    assert update.run([str(zpath)], s, lambda: None, base=inst) == 0
    assert (inst / "app" / "cli.py").read_text() == "v2" and (inst / "run.bat").read_text() == "bat2"
    assert not (inst / "app" / "old_module.py").exists()                 # removed: no longer in the new version
    assert (inst / ".env").read_text() == "SECRET=1"
    assert (inst / "data" / "iv4.db").read_text() == "db"
    assert (inst / "credentials" / "token.json").read_text() == "tok"
    saved = list((inst / "backups").glob("code-*.zip"))
    assert len(saved) == 1
    with zipfile.ZipFile(saved[0]) as z:
        assert z.read("app/cli.py") == b"v1"                              # old program kept for rollback
    assert "Updated" in capsys.readouterr().out


def test_dry_run_changes_nothing(setup):
    inst, zpath, s = setup
    assert update.run([str(zpath), "--dry-run"], s, lambda: None, base=inst) == 0
    assert (inst / "app" / "cli.py").read_text() == "v1" and not (inst / "backups").exists()


def test_refuses_while_agent_runs(setup, capsys):
    inst, zpath, s = setup
    assert update.run([str(zpath)], s, lambda: "99", base=inst) == 1
    assert (inst / "app" / "cli.py").read_text() == "v1" and "run stop" in capsys.readouterr().out


def test_rejects_wrong_zip_and_missing_file(setup, tmp_path, capsys):
    inst, zpath, s = setup
    other = tmp_path / "other.zip"
    with zipfile.ZipFile(other, "w") as z:
        z.writestr("hello.txt", "x")
    assert update.run([str(other)], s, lambda: None, base=inst) == 2
    assert update.run([str(tmp_path / "nope.zip")], s, lambda: None, base=inst) == 2
    assert (inst / "app" / "cli.py").read_text() == "v1"


def test_already_up_to_date(setup, capsys):
    inst, zpath, s = setup
    update.run([str(zpath)], s, lambda: None, base=inst)
    capsys.readouterr()
    assert update.run([str(zpath)], s, lambda: None, base=inst) == 0
    assert "Already up to date" in capsys.readouterr().out


def test_update_shows_version_change(setup, capsys):
    inst, zpath, s = setup
    (inst / "VERSION").write_text("1.0.0\n")
    import zipfile as zf
    with zf.ZipFile(zpath, "a") as z:
        z.writestr("IV4-RealTime-Analytics-main/VERSION", "1.1.0\n")
    assert update.run([str(zpath)], s, lambda: None, base=inst) == 0
    out = capsys.readouterr().out
    assert "1.0.0 -> 1.1.0" in out and "Updated to version 1.1.0" in out
    assert (inst / "VERSION").read_text().strip() == "1.1.0"


def test_rollback_restores_previous_version_and_can_be_undone(setup, capsys):
    inst, zpath, s = setup
    update.run([str(zpath)], s, lambda: None, base=inst)
    assert (inst / "app" / "cli.py").read_text() == "v2"
    assert update.rollback(["--dry-run"], s, lambda: None, base=inst) == 0
    assert (inst / "app" / "cli.py").read_text() == "v2"
    assert update.rollback([], s, lambda: "9", base=inst) == 1                   # agent running: refuse
    assert update.rollback([], s, lambda: None, base=inst) == 0
    assert (inst / "app" / "cli.py").read_text() == "v1" and (inst / "app" / "old_module.py").exists()
    assert (inst / ".env").read_text() == "SECRET=1" and (inst / "data" / "iv4.db").read_text() == "db"
    assert (inst / "credentials" / "token.json").read_text() == "tok"
    assert update.rollback([], s, lambda: None, base=inst) == 0                  # again = undo
    assert (inst / "app" / "cli.py").read_text() == "v2"
    assert update.rollback(["--list"], s, lambda: None, base=inst) == 0
    assert "code-" in capsys.readouterr().out


def test_rollback_without_backups_or_with_bad_file(setup, tmp_path):
    inst, zpath, s = setup
    assert update.rollback([], s, lambda: None, base=inst) == 1
    bad = tmp_path / "bad.zip"
    bad.write_text("x")
    assert update.rollback([str(bad)], s, lambda: None, base=inst) == 2
