#!/bin/sh

set -eu

# Run Git-managed cleanup when the object database grows beyond 1 GiB.
# Git retains all reachable objects and keeps unreachable objects for a
# two-week recovery window so this is safe to run unattended.
threshold_kib="${GIT_OBJECT_CLEANUP_THRESHOLD_KIB:-1048576}"
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "$script_dir/.." && pwd)
git_dir=$(git -C "$repo_root" rev-parse --git-dir)

case "$git_dir" in
    /*) ;;
    *) git_dir="$repo_root/$git_dir" ;;
esac

objects_dir="$git_dir/objects"
size_kib=$(du -sk "$objects_dir" | awk '{print $1}')

if [ "$size_kib" -le "$threshold_kib" ]; then
    printf 'Git object cleanup skipped: %s KiB is not over %s KiB.\n' \
        "$size_kib" "$threshold_kib"
    exit 0
fi

# Do not compete with a checkout, commit, index update, or other maintenance.
active_lock=$(find "$git_dir" -type f \( -name '*.lock' -o -name 'gc.pid' \) -print -quit)
if [ -n "$active_lock" ]; then
    printf 'Git object cleanup skipped: lock exists at %s.\n' "$active_lock"
    exit 0
fi

guard_dir="$objects_dir/auto-cleanup.guard"
if ! mkdir "$guard_dir" 2>/dev/null; then
    printf 'Git object cleanup skipped: another cleanup owns %s.\n' "$guard_dir"
    exit 0
fi

cleanup_guard() {
    rmdir "$guard_dir" 2>/dev/null || true
}
trap cleanup_guard EXIT HUP INT TERM

printf 'Git object database is %s KiB; starting safe garbage collection.\n' \
    "$size_kib"
git -C "$repo_root" gc --quiet --prune='2.weeks.ago'
printf 'Git object cleanup completed; current size is %s KiB.\n' \
    "$(du -sk "$objects_dir" | awk '{print $1}')"
