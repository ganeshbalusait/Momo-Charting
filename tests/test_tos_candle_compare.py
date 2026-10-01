"""scripts/tos_candle_compare.py - the TOS label parser must read what the
CandleCheck study prints, including TOS's habit of showing numbers as 16.0."""
import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "tos_candle_compare.py"
_spec = importlib.util.spec_from_file_location("tos_candle_compare", _PATH)
tcc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tcc)


def test_parses_plain_and_decimal_times_prices_volumes_and_rvol():
    text = ("0: 16:50 C 66.98 V 18999 R 1.2   1: 16.0:35.0 C $66.655 V 100 R -0.4\n"
            "2: 9:5 C 1,234.50 V 1,500,000 R NaN")
    out = tcc.parse_tos(text)
    assert out["16:50"] == {"close": 66.98, "volume": 18999.0, "rvol": 1.2}
    assert out["16:35"] == {"close": 66.655, "volume": 100.0, "rvol": -0.4}
    assert out["09:05"] == {"close": 1234.5, "volume": 1500000.0, "rvol": None}


def test_rvol_is_optional():
    assert tcc.parse_tos("0: 10:00 C 5 V 10")["10:00"]["rvol"] is None


def test_study_prints_ten_candles():
    assert tcc.TOS_STUDY.count("AddLabel(") == 10
