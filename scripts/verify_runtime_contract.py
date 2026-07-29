from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import DATABASE_PATH, settings


def main() -> int:
    profiles = settings.available_profiles(settings.execution_mode)
    profile_ids = [profile.profile_id.lower() for profile in profiles]
    stock_profile = settings.stock_account_profile_id("paper")
    mag7_option_profile = settings.option_account_profile_id("paper")
    watchlist_option_profile = settings.watchlist_option_account_profile_id("paper")
    errors: list[str] = []

    if settings.execution_mode != "paper":
        errors.append("execution mode must remain paper for automated verification")
    if settings.allow_live_trading:
        errors.append("ALLOW_LIVE_TRADING must remain false")
    if not profiles:
        errors.append("no configured Alpaca profiles were found")
    for profile in profiles:
        if not profile.key or not profile.secret:
            errors.append(f"profile {profile.profile_id} has incomplete credentials")
    for required in (stock_profile, mag7_option_profile, watchlist_option_profile):
        if required not in profile_ids:
            errors.append(f"required routed profile {required} is not configured")
    if len({stock_profile, mag7_option_profile, watchlist_option_profile}) != 3:
        errors.append("stock, MAG7 option, and watchlist option routes must use distinct profiles")
    if not DATABASE_PATH.parent.exists():
        errors.append(f"database directory does not exist: {DATABASE_PATH.parent}")

    payload = {
        "ok": not errors,
        "executionMode": settings.execution_mode,
        "allowLiveTrading": settings.allow_live_trading,
        "configuredProfiles": profile_ids,
        "routing": {
            "stock": stock_profile,
            "mag7Options": mag7_option_profile,
            "watchlistOptions": watchlist_option_profile,
        },
        "databasePath": str(DATABASE_PATH),
        "errors": errors,
    }
    print(json.dumps(payload, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
