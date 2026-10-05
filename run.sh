#!/usr/bin/env sh
# IV4 Data Agent - one command (Linux/macOS). Same commands as run.bat:
#   ./run.sh            setup (first time) + checks + start
#   ./run.sh status | check | metrics ... | benchmark | test | help
set -e
cd "$(dirname "$0")"
PY=$(command -v python3 || command -v python || true)
if [ -z "$PY" ]; then echo "Python 3.10+ not found"; exit 9; fi
DEV=""
[ "$1" = "test" ] && DEV="--dev"
VPY=$("$PY" scripts/bootstrap.py $DEV)
export PYTHONUTF8=1
exec "$VPY" -m app "$@"
