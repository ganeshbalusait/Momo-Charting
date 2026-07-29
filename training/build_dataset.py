from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from backtester import Backtester
from config import BASE_DIR


DEFAULT_SYMBOLS = ["AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA"]


def build_dataset(
    symbols: list[str] | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    output_path: Path | None = None,
) -> tuple[pd.DataFrame, Path]:
    tz = ZoneInfo("America/New_York")
    start = start or datetime(2020, 1, 1, tzinfo=tz)
    end = end or datetime.now(tz=tz)
    dataset_path = output_path or (BASE_DIR / "artifacts" / "training_dataset.csv")

    backtester = Backtester()
    _, trades = backtester.run(symbols or DEFAULT_SYMBOLS, start=start, end=end)
    dataset_path.parent.mkdir(parents=True, exist_ok=True)
    trades.to_csv(dataset_path, index=False)
    return trades, dataset_path


if __name__ == "__main__":
    frame, path = build_dataset()
    print(f"rows={len(frame)}")
    print(f"dataset={path}")
