#!/usr/bin/env python3
"""Conservative, cross-platform cleanup for known disposable repository data."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


MIB = 1024 * 1024


@dataclass(frozen=True)
class CleanupAction:
    kind: str
    path: Path
    size_bytes: int


def directory_size(path: Path) -> int:
    total = 0
    if not path.exists() or path.is_symlink():
        return total
    for child in path.rglob("*"):
        try:
            if child.is_file() and not child.is_symlink():
                total += child.stat().st_size
        except FileNotFoundError:
            continue
    return total


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=True))
        return True
    except (FileNotFoundError, ValueError):
        return False


def _regular_large_files(paths: Iterable[Path], threshold_bytes: int) -> list[CleanupAction]:
    actions: list[CleanupAction] = []
    for path in paths:
        try:
            if path.is_file() and not path.is_symlink():
                size = path.stat().st_size
                if size > threshold_bytes:
                    actions.append(CleanupAction("delete-file", path, size))
        except FileNotFoundError:
            continue
    return actions


def resolve_git_dir(repo_root: Path) -> Path | None:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "--absolute-git-dir"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip()).resolve()


def git_disposable_size(git_dir: Path) -> int:
    """Return loose/temporary object bytes; packed reachable history is excluded."""
    objects = git_dir / "objects"
    total = 0
    if not objects.is_dir():
        return total
    for fanout in objects.iterdir():
        if len(fanout.name) != 2 or not fanout.is_dir() or fanout.is_symlink():
            continue
        for obj in fanout.iterdir():
            try:
                if obj.is_file() and not obj.is_symlink():
                    total += obj.stat().st_size
            except FileNotFoundError:
                continue
    return total


def collect_plan(repo_root: Path, threshold_bytes: int) -> list[CleanupAction]:
    root = repo_root.resolve(strict=True)
    actions: list[CleanupAction] = []

    # Only timestamped backup databases are eligible. The live trades.db is protected.
    backup_paths = list((root / "database").glob("trades.before-*.db"))
    backup_paths += list((root / "artifacts").glob("trades-before-*.db"))
    actions.extend(_regular_large_files(backup_paths, threshold_bytes))

    # Only known log locations are eligible; source and data files are never scanned.
    log_paths = list(root.glob("*.log"))
    for log_root in (root / "artifacts", root / ".vite"):
        if log_root.is_dir() and not log_root.is_symlink():
            log_paths.extend(log_root.rglob("*.log"))
    actions.extend(_regular_large_files(log_paths, threshold_bytes))

    # Generated Python bytecode is removed as a group only when the group exceeds the limit.
    pycache_dirs = []
    for path in root.rglob("__pycache__"):
        relative_parts = path.relative_to(root).parts
        in_protected_tree = (
            relative_parts[:1] in ((".git",), (".venv",), (".venv-macos",))
            or relative_parts[:2] == ("frontend", "node_modules")
        )
        if path.is_dir() and not path.is_symlink() and not in_protected_tree:
            pycache_dirs.append(path)
    pycache_sizes = [(path, directory_size(path)) for path in pycache_dirs]
    if sum(size for _, size in pycache_sizes) > threshold_bytes:
        actions.extend(
            CleanupAction("delete-pycache", path, size)
            for path, size in pycache_sizes
            if size > 0
        )

    # These exact directories contain reproducible test or bundler caches.
    cache_dirs = (
        root / ".pytest_cache",
        root / ".vite",
        root / "frontend" / ".vite",
        root / "frontend" / "node_modules" / ".vite",
        root / "frontend" / ".tmp-oi-finder-build",
    )
    for path in cache_dirs:
        size = directory_size(path)
        if size > threshold_bytes:
            actions.append(CleanupAction("delete-tree", path, size))

    git_dir = resolve_git_dir(root)
    if git_dir is not None:
        size = git_disposable_size(git_dir)
        if size > threshold_bytes:
            actions.append(CleanupAction("git-gc", git_dir / "objects", size))

    tree_paths = [
        action.path
        for action in actions
        if action.kind in ("delete-tree", "delete-pycache")
    ]
    actions = [
        action
        for action in actions
        if action.kind != "delete-file"
        or not any(_inside(action.path, tree_path) for tree_path in tree_paths)
    ]
    return sorted(actions, key=lambda action: (action.kind, str(action.path)))


def _git_has_lock(git_dir: Path) -> Path | None:
    for pattern in ("*.lock", "gc.pid"):
        for path in git_dir.rglob(pattern):
            if path.is_file():
                return path
    return None


def apply_plan(
    repo_root: Path,
    actions: list[CleanupAction],
    git_prune: str,
) -> tuple[int, list[str]]:
    root = repo_root.resolve(strict=True)
    reclaimed = 0
    messages: list[str] = []
    removed_pycache_count = 0
    removed_pycache_bytes = 0

    for action in actions:
        if action.kind == "git-gc":
            git_dir = action.path.parent.parent
            lock = _git_has_lock(git_dir)
            if lock is not None:
                messages.append(f"SKIP Git cleanup: active lock {lock}")
                continue
            subprocess.run(
                ["git", "-C", str(root), "gc", "--quiet", f"--prune={git_prune}"],
                check=True,
            )
            after = git_disposable_size(git_dir)
            reclaimed += max(0, action.size_bytes - after)
            messages.append(f"CLEAN Git loose/temporary objects ({format_bytes(action.size_bytes)})")
            continue

        if not _inside(action.path, root) or action.path.is_symlink():
            messages.append(f"SKIP unsafe path: {action.path}")
            continue
        try:
            if action.kind == "delete-file":
                action.path.unlink()
            elif action.kind in ("delete-tree", "delete-pycache"):
                shutil.rmtree(action.path)
            else:
                raise ValueError(f"Unsupported cleanup action: {action.kind}")
            reclaimed += action.size_bytes
            if action.kind == "delete-pycache":
                removed_pycache_count += 1
                removed_pycache_bytes += action.size_bytes
            else:
                messages.append(f"DELETE {action.path} ({format_bytes(action.size_bytes)})")
        except FileNotFoundError:
            messages.append(f"SKIP already removed: {action.path}")

    if removed_pycache_count:
        messages.append(
            f"DELETE {removed_pycache_count} __pycache__ directories "
            f"({format_bytes(removed_pycache_bytes)})"
        )
    return reclaimed, messages


def format_bytes(size: int) -> str:
    if size >= 1024**3:
        return f"{size / 1024**3:.2f} GiB"
    if size >= MIB:
        return f"{size / MIB:.2f} MiB"
    if size >= 1024:
        return f"{size / 1024:.2f} KiB"
    return f"{size} bytes"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Delete only allowlisted disposable data above a size threshold."
    )
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--threshold-mb", type=int, default=100)
    parser.add_argument("--apply", action="store_true", help="Perform cleanup; otherwise dry-run")
    parser.add_argument(
        "--git-prune",
        default="1.day.ago",
        choices=("1.day.ago", "2.weeks.ago", "now"),
        help="Git recovery grace period (automatic default: 1 day)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.threshold_mb <= 0:
        print("threshold must be greater than zero", file=sys.stderr)
        return 2

    repo_root = args.repo_root.resolve(strict=True)
    plan = collect_plan(repo_root, args.threshold_mb * MIB)
    total = sum(action.size_bytes for action in plan)

    if not plan:
        print(f"Nothing eligible exceeds {args.threshold_mb} MiB.")
        return 0

    if not args.apply:
        print(f"DRY RUN: {len(plan)} allowlisted item(s), up to {format_bytes(total)}:")
        pycache_actions = [action for action in plan if action.kind == "delete-pycache"]
        visible_actions = [action for action in plan if action.kind != "delete-pycache"]
        for action in visible_actions:
            print(f"  {action.kind}: {action.path} ({format_bytes(action.size_bytes)})")
        if pycache_actions:
            pycache_size = sum(action.size_bytes for action in pycache_actions)
            print(
                f"  delete-pycache: {len(pycache_actions)} directories "
                f"({format_bytes(pycache_size)})"
            )
        print("Run again with --apply to perform cleanup.")
        return 0

    repo_key = hashlib.sha256(str(repo_root).encode()).hexdigest()[:16]
    lock_path = Path(tempfile.gettempdir()) / f"agenticai-storage-cleanup-{repo_key}.lock"
    try:
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        print(f"Cleanup skipped because another run owns {lock_path}", file=sys.stderr)
        return 3

    try:
        os.write(lock_fd, f"pid={os.getpid()}\n".encode())
        os.close(lock_fd)
        reclaimed, messages = apply_plan(repo_root, plan, args.git_prune)
        for message in messages:
            print(message)
        print(f"Cleanup completed; approximately {format_bytes(reclaimed)} reclaimed.")
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
