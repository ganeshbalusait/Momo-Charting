# Safe storage cleanup on macOS and Windows

The cleanup tools use a strict allowlist. A large file is **not** deleted merely
because it exceeds 100 MiB. Source code, the live database, credentials,
environments, dependencies, and production output are always protected.

## What becomes eligible above 100 MiB

- Loose or temporary Git objects. Cleanup is performed by `git gc`; object
  files are never deleted directly.
- Timestamped database backups matching:
  - `database/trades.before-*.db`
  - `artifacts/trades-before-*.db`
- Individual `.log` files in the project root, `artifacts`, or `.vite`.
- Project `__pycache__` directories when their combined size exceeds 100 MiB.
  Bytecode inside `.venv`, `.venv-macos`, and `node_modules` is excluded.
- Exact reproducible cache directories such as `.pytest_cache`, `.vite`,
  `frontend/node_modules/.vite`, and `frontend/.tmp-oi-finder-build` when an
  individual directory exceeds 100 MiB.

The following are never selected: `database/trades.db`, `.env`, OAuth tokens,
`.venv`, `.venv-macos`, `frontend/node_modules`, `frontend/dist`, or source
folders.

## macOS

Preview without deleting anything:

```zsh
./scripts/cleanup_storage_macos.sh
```

Perform the allowlisted cleanup with the automatic one-day Git recovery grace
period:

```zsh
./scripts/cleanup_storage_macos.sh --apply
```

For a one-time cleanup while no Git command is running, remove all unreachable
Git objects immediately:

```zsh
./scripts/cleanup_storage_macos.sh --apply --git-prune now
```

## Windows PowerShell

Preview without deleting anything:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\cleanup_storage_windows.ps1
```

Perform cleanup:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\cleanup_storage_windows.ps1 -Apply
```

Perform a one-time immediate Git prune while no Git command is running:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\cleanup_storage_windows.ps1 -Apply -GitPrune now
```

Use `-ThresholdMB 250` or `--threshold-mb 250` to override the default.

## Automatic operation

Repository hooks run the applied cleanup after `git add`/index changes,
commits, merges, and history rewrites. Enable the versioned hooks once per
clone:

```text
git config core.hooksPath scripts/git-hooks
```

Automatic runs use the cross-platform `cleanup_storage_auto.sh` dispatcher, a
one-day Git recovery grace period, and write their output to
`/tmp/agenticai-trading-storage-cleanup.log` when Git is run from macOS or Git
Bash. Cleanup skips Git maintenance whenever another Git lock is present.

The hooks intentionally do not delete the live trading database or remove a
virtual environment, even when either is larger than 100 MiB.
