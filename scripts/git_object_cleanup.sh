#!/bin/sh

# Backward-compatible entry point. The consolidated cleanup defaults to the
# requested 100 MiB threshold and uses a one-day Git recovery grace period.
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$script_dir/cleanup_storage_auto.sh" --apply "$@"
