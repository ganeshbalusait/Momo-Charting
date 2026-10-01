r"""Nightly disaster backup: the trade journal, the secrets, and the code.

WHY THIS EXISTS (2026-08-28): everything that matters lived in exactly one
place - on one laptop's one disk. The git repo has NO remote, database/trades.db
(1GB of trade history) exists nowhere else, and .env holds every broker and API
key. A disk failure was total, permanent loss. This repo has already survived
one truncation of trades.db (see the auth-rebuild incident) - that time there
was a copy to rebuild from. There would not be a second time.

WHAT IT WRITES, per run, into <destination>/AGX-Backups/:
    trades-YYYYMMDD.db.zip   consistent snapshot of the trade journal
    secrets-YYYYMMDD.zip     .env + Schwab OAuth tokens + watchlists
    code-YYYYMMDD.bundle     the ENTIRE git repo, all branches, one file
                             (restore: git clone code-YYYYMMDD.bundle agx)

THE ONE SUBTLE PART: trades.db is copied with SQLite's `VACUUM INTO`, not a
file copy. The app writes to that database continuously; a plain copy of a live
SQLite file can capture a half-written page and produce a backup that only
LOOKS fine until the day you need it. VACUUM INTO takes a read lock and emits a
consistent, compacted snapshot (measured: 18.5s, 1023MB -> 976MB, integrity_check
ok). Every snapshot is integrity-checked here before it is kept.

DESTINATION is auto-detected in priority order (first that exists wins):
Google Drive, then OneDrive, then C:\AGX-Backups as a last-resort local
staging folder. NOTE: the local fallback is NOT a real backup - it is on the
same disk as the original. It only becomes protection once a cloud folder
exists and syncs it off the machine.
"""
from __future__ import annotations

import datetime as _dt
import os
import sqlite3
import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

#: Keep this many of each artifact. The journal and secrets are small enough to
#: keep a week of; the code bundle is large and GitHub is its primary home.
KEEP = {"trades": 7, "secrets": 7, "code": 3}

#: Files worth more than the machine they sit on. Missing ones are skipped with
#: a note rather than failing the run - a backup that aborts because one
#: optional token is absent is a backup that silently stops happening.
SECRET_FILES = [
    ".env",
    "artifacts/schwab_token.json",
    "artifacts/schwab_trading_token.json",
    "watchlist.txt",
    #: Per-user vault key and per-user Schwab logins (added 2026-09-04: a
    #: Drive-only rebuild would otherwise lose every user's saved keys).
    "artifacts/user_credentials.key",
    "artifacts/user_tokens",
]

#: Files OUTSIDE the repo that a rebuilt machine cannot recreate without
#: manual Cloudflare work: the tunnel credentials that make app.agxtrade.com
#: point at this box (trader's question, 2026-08-30: "will it work on any
#: laptop?" - the code would, but the public address would not without these).
#: Zipped under cloudflared/ inside the secrets archive.
HOME_SECRET_GLOBS = [
    (".cloudflared", "config.yml"),
    (".cloudflared", "cert.pem"),
    (".cloudflared", "*.json"),
]


def log(message: str) -> None:
    stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


#: GOOGLE DRIVE ONLY, by the trader's instruction (2026-08-28: "Don't use one
#: drive everything will be Google Drive"). OneDrive is deliberately NOT a
#: fallback - silently diverting a backup to a cloud he did not choose is worse
#: than failing loudly, and its free tier (5GB, already 1.1GB used) would fill
#: and then stop syncing without telling anyone. His Drive has 2TB.
DRIVE_FOLDERS = ("My Drive", "Google Drive", "GoogleDrive")


def destination() -> Path:
    """The Google Drive folder, or clearly-labelled local staging if absent.

    Returns the staging path when Drive for Desktop is not installed, so a
    backup still HAPPENS (a run that produces nothing is the worst outcome) -
    but main() shouts about it, because a copy on the same disk protects
    against nothing.
    """
    home = Path(os.environ.get("USERPROFILE", str(Path.home())))
    candidates = [home / name for name in DRIVE_FOLDERS]
    # Drive for Desktop's DEFAULT mount is a virtual drive letter (found live
    # 2026-08-30: G: My Drive), not a folder under the user profile - the
    # profile-folder layout only exists in "mirror" mode. Scan letters too.
    for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
        candidates.append(Path(f"{letter}:/") / "My Drive")
    for base in candidates:
        try:
            if base.exists():
                target = base / "AGX-Backups"
                target.mkdir(parents=True, exist_ok=True)
                return target
        except OSError:
            continue  # an unready drive letter must not kill the backup
    fallback = Path("C:/AGX-Backups-LOCAL-ONLY/AGX-Backups")
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def warn_if_not_syncing(out_dir: Path) -> None:
    """A cloud FOLDER existing does not mean anything is being UPLOADED.

    Found the hard way on 2026-08-28: the OneDrive folder was present and the
    account signed in, so the destination looked like real off-machine
    protection - but the OneDrive client was not running, so three nightly
    backups would have sat on the same disk as the originals, protecting
    nothing, while every log line said success. Same shape as every other
    truthy-but-meaningless status flag in this project: the writer reported an
    INTENTION (a path) and the reader believed an OUTCOME (a cloud copy).

    So check the thing that actually moves bytes - the sync client process -
    and say so loudly when it is absent.
    """
    clients = {"my drive": "GoogleDriveFS", "google drive": "GoogleDriveFS",
               "googledrive": "GoogleDriveFS"}
    lowered = str(out_dir).lower()
    process = next((proc for token, proc in clients.items() if token in lowered), None)
    if process is None:
        return  # local staging: main() already warns about that case
    try:
        result = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {process}.exe"],
                                capture_output=True, text=True, timeout=30)
        running = f"{process}.exe".lower() in (result.stdout or "").lower()
    except Exception:  # noqa: BLE001 - never fail a backup over a status probe
        return
    if not running:
        log(f"WARNING: {process} is NOT RUNNING. Files will be written to the")
        log("         sync folder but NOTHING WILL REACH THE CLOUD until it")
        log("         starts - these copies are on the same disk as the originals.")


def backup_database(out_dir: Path, day: str) -> bool:
    """Consistent snapshot of the live trade journal, verified then zipped."""
    from config import DATABASE_PATH  # imported late: keeps --help cheap

    source = Path(DATABASE_PATH)
    if not source.exists():
        log(f"SKIP database: not found at {source}")
        return False

    staging = REPO / "artifacts" / f"_backup_{day}.db"
    staging.parent.mkdir(parents=True, exist_ok=True)
    if staging.exists():
        staging.unlink()

    log(f"snapshotting {source.name} ({source.stat().st_size/1e6:.0f} MB) ...")
    con = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=120)
    try:
        con.execute("VACUUM INTO ?", (str(staging),))
    finally:
        con.close()

    check = sqlite3.connect(str(staging))
    try:
        verdict = check.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        check.close()
    if verdict != "ok":
        # A corrupt snapshot must never overwrite a good one, and must never be
        # reported as a success - that is how silent backup rot happens.
        staging.unlink(missing_ok=True)
        log(f"FAILED database: integrity_check said {verdict!r} - snapshot discarded")
        return False

    archive = out_dir / f"trades-{day}.db.zip"
    log(f"compressing -> {archive.name} ...")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        zf.write(staging, arcname="trades.db")
    staging.unlink(missing_ok=True)
    log(f"database OK: {archive.name} ({archive.stat().st_size/1e6:.0f} MB, integrity ok)")
    return True


def backup_secrets(out_dir: Path, day: str) -> bool:
    archive = out_dir / f"secrets-{day}.zip"
    written = []
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in SECRET_FILES:
            path = REPO / name
            if path.is_dir():
                for child in sorted(path.rglob("*")):
                    if child.is_file():
                        rel = child.relative_to(REPO).as_posix()
                        zf.write(child, arcname=rel)
                        written.append(rel)
            elif path.exists():
                zf.write(path, arcname=name)
                written.append(name)
            else:
                log(f"  note: {name} absent, skipped")
    home = Path(os.environ.get("USERPROFILE", str(Path.home())))
    with zipfile.ZipFile(archive, "a", zipfile.ZIP_DEFLATED) as zf:
        for folder, pattern in HOME_SECRET_GLOBS:
            for path in sorted((home / folder).glob(pattern)):
                if path.is_file():
                    zf.write(path, arcname=f"cloudflared/{path.name}")
                    written.append(f"cloudflared/{path.name}")
    if not written:
        archive.unlink(missing_ok=True)
        log("FAILED secrets: nothing to back up")
        return False
    log(f"secrets OK: {archive.name} ({len(written)} files)")
    return True


def backup_code(out_dir: Path, day: str) -> bool:
    """One file containing every branch and all history - clone it to restore."""
    bundle = out_dir / f"code-{day}.bundle"
    result = subprocess.run(
        ["git", "bundle", "create", str(bundle), "--all"],
        cwd=str(REPO), capture_output=True, text=True,
    )
    if result.returncode != 0 or not bundle.exists():
        log(f"FAILED code bundle: {result.stderr.strip()[:200]}")
        return False
    log(f"code OK: {bundle.name} ({bundle.stat().st_size/1e6:.0f} MB, all branches)")
    return True


def prune(out_dir: Path) -> None:
    """Keep the newest N of each kind so the folder cannot grow without bound."""
    for prefix, keep in KEEP.items():
        matches = sorted(out_dir.glob(f"{prefix}-*"), key=lambda p: p.name, reverse=True)
        for stale in matches[keep:]:
            try:
                stale.unlink()
                log(f"pruned {stale.name}")
            except OSError as exc:
                log(f"could not prune {stale.name}: {exc}")


def main() -> int:
    sys.path.insert(0, str(REPO))
    out_dir = destination()
    day = _dt.date.today().strftime("%Y%m%d")
    log(f"backup -> {out_dir}")
    if "LOCAL-ONLY" in str(out_dir):
        log("WARNING: Google Drive for Desktop is not installed, so there is no")
        log("         Drive folder to write to. This copy is on the SAME DISK as")
        log("         the original and does NOT protect against drive failure.")
        log("         Install Drive for Desktop and sign in; this script will")
        log("         switch to it automatically on the next run, no edit needed.")

    warn_if_not_syncing(out_dir)
    results = {
        "database": backup_database(out_dir, day),
        "secrets": backup_secrets(out_dir, day),
        "code": backup_code(out_dir, day),
    }
    prune(out_dir)

    failed = [name for name, ok in results.items() if not ok]
    if failed:
        log(f"FINISHED WITH FAILURES: {', '.join(failed)}")
        return 1
    log("all three backups completed and verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
