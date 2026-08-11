#!/bin/sh

# Portable dispatcher used by Git hooks on macOS, Linux, and Git for Windows.
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "$script_dir/.." && pwd)

case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*)
        if [ -x "$repo_root/.venv/Scripts/python.exe" ]; then
            python_bin="$repo_root/.venv/Scripts/python.exe"
        elif command -v python >/dev/null 2>&1; then
            python_bin=$(command -v python)
        else
            printf '%s\n' "Python 3 was not found; storage cleanup skipped." >&2
            exit 69
        fi
        ;;
    *)
        if [ -x "$repo_root/.venv-macos/bin/python" ]; then
            python_bin="$repo_root/.venv-macos/bin/python"
        elif [ -x "$repo_root/.venv/bin/python" ]; then
            python_bin="$repo_root/.venv/bin/python"
        elif command -v python3 >/dev/null 2>&1; then
            python_bin=$(command -v python3)
        elif command -v python >/dev/null 2>&1; then
            python_bin=$(command -v python)
        else
            printf '%s\n' "Python 3 was not found; storage cleanup skipped." >&2
            exit 69
        fi
        ;;
esac

exec "$python_bin" "$script_dir/cleanup_safe_storage.py" \
    --repo-root "$repo_root" "$@"
