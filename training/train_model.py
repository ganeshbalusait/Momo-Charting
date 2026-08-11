from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from config import BASE_DIR, settings
from predictive_model import PredictiveTradeModel
from training.build_dataset import DEFAULT_SYMBOLS, build_dataset


def train_model(
    symbols: list[str] | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    dataset_path: Path | None = None,
) -> dict:
    tz = ZoneInfo("America/New_York")
    start = start or datetime(2020, 1, 1, tzinfo=tz)
    end = end or datetime.now(tz=tz)
    target_dataset_path = dataset_path or (BASE_DIR / "artifacts" / "training_dataset.csv")

    if target_dataset_path.exists():
        dataset = pd.read_csv(target_dataset_path)
    else:
        dataset, _ = build_dataset(symbols or DEFAULT_SYMBOLS, start=start, end=end, output_path=target_dataset_path)

    model = PredictiveTradeModel(settings.ai.predictive_model_path)
    metrics = model.fit(dataset)
    return {
        "artifact": str(model.artifact_path),
        "dataset": str(target_dataset_path),
        **metrics,
    }


if __name__ == "__main__":
    result = train_model()
    for key, value in result.items():
        print(f"{key}={value}")
