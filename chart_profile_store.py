"""Per-user chart indicator profiles on the server (2026-10-08).

His ask: "Save D for all tickers" must reach the phone AND the desktop. The
profile lived only in each browser's localStorage, so a save on one device
never reached the other (and on the phone the save failed outright: the MomX
board copy had filled the ~5 MB storage).

One JSON file per account under artifacts/user_chart_profiles/, keyed by the
account's email (lower-cased, hashed for the file name). The browser keeps its
own copy too; whichever ``savedAt`` is newer wins on load.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent / "artifacts" / "user_chart_profiles"
MAX_PROFILE_BYTES = 256 * 1024     # one timeframe's settings + options (~20 KB today)
MAX_FILE_BYTES = 2 * 1024 * 1024   # every timeframe for one account
_KEY = re.compile(r"^[A-Za-z0-9:_\-.]{1,64}$")
_LOCK = threading.Lock()


def _path(email: str, root: Path | None = None) -> Path | None:
    who = str(email or "").strip().lower()
    if not who:
        return None
    return (root or ROOT) / (hashlib.sha256(who.encode("utf-8")).hexdigest()[:24] + ".json")


def load_profiles(email: str, root: Path | None = None) -> dict[str, Any]:
    path = _path(email, root)
    if path is None or not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    profiles = data.get("profiles") if isinstance(data, dict) else None
    return profiles if isinstance(profiles, dict) else {}


def save_profile(email: str, key: str, profile: Any, root: Path | None = None) -> dict[str, Any]:
    """Store one timeframe's profile; returns the account's profiles. Raises ValueError on bad input."""
    path = _path(email, root)
    if path is None:
        raise ValueError("no account")
    if not isinstance(key, str) or not _KEY.match(key):
        raise ValueError("bad profile key")
    if not isinstance(profile, dict) or not isinstance(profile.get("indicatorSettings"), dict) \
            or not isinstance(profile.get("indicatorOptions"), dict):
        raise ValueError("profile needs indicatorSettings and indicatorOptions")
    if len(json.dumps(profile)) > MAX_PROFILE_BYTES:
        raise ValueError("profile too large")
    with _LOCK:
        profiles = load_profiles(email, root)
        profiles[key] = {
            "indicatorSettings": profile["indicatorSettings"],
            "indicatorOptions": profile["indicatorOptions"],
            "savedAt": str(profile.get("savedAt") or ""),
        }
        body = json.dumps({"profiles": profiles}, separators=(",", ":"))
        if len(body) > MAX_FILE_BYTES:
            raise ValueError("too many profiles")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
        temporary.write_text(body, encoding="utf-8")
        os.replace(temporary, path)
    return profiles
