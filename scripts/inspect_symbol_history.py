from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Read stored scanner events for one symbol")
    parser.add_argument("symbol")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    database_path = Path(__file__).resolve().parents[1] / "database" / "trades.db"
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        """
        SELECT id, scan_date, scanned_at, source, symbol, last_price,
               one_hour_close_change_pct, four_hour_volume_change_pct,
               trigger_source, setup_name, raw_json
        FROM scanner_history
        WHERE UPPER(symbol) = ?
        ORDER BY scanned_at DESC
        LIMIT ?
        """,
        (args.symbol.strip().upper(), max(args.limit, 1)),
    ).fetchall()
    print(json.dumps([dict(row) for row in rows], indent=2, default=str))
    connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
