#!/bin/zsh

set -euo pipefail

SCRIPT_DIR="${0:A:h}"
PROJECT_DIR="${SCRIPT_DIR:h}"
LOG_DIR="$PROJECT_DIR/artifacts/logs"
FRONTEND_INDEX="$PROJECT_DIR/frontend/dist/index.html"

mkdir -p "$LOG_DIR"
umask 077

if [[ -x "$PROJECT_DIR/.venv-macos/bin/python" ]]; then
  PYTHON_BIN="$PROJECT_DIR/.venv-macos/bin/python"
elif [[ -x "$PROJECT_DIR/.venv/bin/python" ]]; then
  PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
else
  print -u2 "Python virtual environment is missing. Expected .venv-macos or .venv."
  exit 78
fi

if [[ ! -f "$FRONTEND_INDEX" ]]; then
  print -u2 "Production frontend is missing. Run: cd frontend && npm run build"
  exit 78
fi

cd "$PROJECT_DIR"
export PYTHONPYCACHEPREFIX="${TMPDIR:-/tmp}/agenticai-trading-pycache"

# Keep the service alive while the Mac is connected to AC power. launchd
# restarts the process after a crash and starts it again after user login.
exec /usr/bin/caffeinate -s "$PYTHON_BIN" -u "$PROJECT_DIR/api_server.py"
