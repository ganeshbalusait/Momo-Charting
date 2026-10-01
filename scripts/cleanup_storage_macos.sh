#!/bin/zsh

set -euo pipefail

SCRIPT_DIR="${0:A:h}"
PROJECT_DIR="${SCRIPT_DIR:h}"

if [[ -x "$PROJECT_DIR/.venv-macos/bin/python" ]]; then
  PYTHON_BIN="$PROJECT_DIR/.venv-macos/bin/python"
elif [[ -x "$PROJECT_DIR/.venv/bin/python" ]]; then
  PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3)"
else
  print -u2 "Python 3 was not found."
  exit 69
fi

exec "$PYTHON_BIN" "$SCRIPT_DIR/cleanup_safe_storage.py" \
  --repo-root "$PROJECT_DIR" "$@"
