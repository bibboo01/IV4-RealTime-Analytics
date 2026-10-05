"""
First-run setup, called by run.bat / run.sh before every start.
Standard library only (it runs before dependencies are installed).

  * creates .venv               (only if missing)
  * pip installs requirements   (only if requirements*.txt changed since last time)
  * creates .env from .env.example (only if missing - never overwrites)

Prints the path of the venv's python on the last line so the launcher can use it.
Exit code != 0 means setup failed; the message says why.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / ".venv"
STAMP = VENV / ".requirements.sha256"
MIN_PY = (3, 10)


def say(msg: str) -> None:
    print(f"[setup] {msg}", file=sys.stderr, flush=True)


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def requirements_hash(dev: bool) -> str:
    h = hashlib.sha256()
    files = ["requirements.txt"] + (["requirements-dev.txt"] if dev else [])
    for name in files:
        p = ROOT / name
        if p.exists():
            h.update(p.read_bytes())
    h.update(f"dev={dev}".encode())
    return h.hexdigest()


def main() -> int:
    dev = "--dev" in sys.argv
    if sys.version_info < MIN_PY:
        say(f"Python {sys.version.split()[0]} is too old - need {MIN_PY[0]}.{MIN_PY[1]}+ "
            "(download from https://www.python.org/downloads/windows/ and tick 'Add to PATH')")
        return 2

    py = venv_python()
    if not py.exists():
        say(f"creating virtual environment in {VENV} ...")
        venv.EnvBuilder(with_pip=True, clear=False).create(VENV)

    want = requirements_hash(dev)
    have = STAMP.read_text().strip() if STAMP.exists() else ""
    if want != have:
        req = "requirements-dev.txt" if dev else "requirements.txt"
        say(f"installing dependencies from {req} (first run or requirements changed) ...")
        cmd = [str(py), "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", str(ROOT / req)]
        r = subprocess.run(cmd, cwd=ROOT)
        if r.returncode != 0:
            say("pip install failed - check the internet connection / proxy, then run again")
            return r.returncode
        STAMP.write_text(want)

    env, example = ROOT / ".env", ROOT / ".env.example"
    if not env.exists() and example.exists():
        shutil.copyfile(example, env)
        say("created .env from .env.example - review IV4_INCOMING_DIR / IV4_SENSORS")

    print(py)
    return 0


if __name__ == "__main__":
    sys.exit(main())
