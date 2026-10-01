"""The close gate must see the bar that is happening.

2026-09-01 09:54 ET, AAPL +1.49% with 3.1x hourly volume and seven bullish
confirmations, blocked by `pricechange:close:1h` while the rest of Mag7 was
red. Measured on the live tape:

    WITH the developing 09:00 bar   07:00 316.69 -> 09:00 320.49   +1.200%  PASS
    completed bars only             06:00 315.87 -> 08:00 316.56   +0.220%  BLOCK

The whole opening drive - 316.56 to 320.49 - lives in the 09:00 bar the gate
refused to read, so between the open and 10:00 the gate judged the day on
PREMARKET and blocked the entire board.

The 2026-08-31 change that dropped the still-forming bucket was right for
VOLUME and wrong for CLOSE, and applying it to both is the defect:

  * volume ACCUMULATES - a 23-minute bucket holding 67k can never beat a full
    bucket's 162k, so comparing them is unwinnable and the developing bar must
    go (that was the real 09:23 blackout).
  * close does NOT accumulate - a developing bar's close IS the live price. It
    is not an unfair comparison, it is the only bar that knows the stock moved.
"""
import io
import os

from momx import scan


def _bars(closes, *, start=1_800_000_000, step=3600):
    return [
        {"time": start + i * step, "close": c, "open": c, "high": c, "low": c, "volume": 1000}
        for i, c in enumerate(closes)
    ]


def test_close_gate_reads_the_developing_bar():
    """AAPL's real shape: flat premarket, then the open drives it."""
    # 06:00 315.87, 07:00 316.69, 08:00 316.56, 09:00 320.49 (still forming)
    tape = _bars([315.87, 316.69, 316.56, 320.49])
    assert scan.price_change_gate(tape, "close", scan.CLOSE_CHANGE_MIN_PCT,
                                  scan.CLOSE_CHANGE_BARS_AGO) is True

    # Dropping the live bar is what blocked it: 315.87 -> 316.56 is +0.22%.
    stale = tape[:-1]
    assert scan.price_change_gate(stale, "close", scan.CLOSE_CHANGE_MIN_PCT,
                                  scan.CLOSE_CHANGE_BARS_AGO) is False


def test_both_gates_read_the_live_candle():
    """The trader's rule, 2026-09-01: "TOS will look live candle not full
    candle closed", then explicitly "Volume 4h also live candle". He owns the
    thinkScript this mirrors, so both gates read the full tape.

    Recorded here because it CONTRADICTS the 2026-08-31 finding that dropping
    the developing bucket is what matched TOS during the 09:23 blackout. Both
    cannot be true; the likely explanation is that the blackout was a
    bucket-ALIGNMENT problem and dropping the bar fixed it by accident. This
    test pins the decision so a future reader changes it deliberately, with
    evidence, rather than by reflex.
    """
    source = io.open(
        os.path.join(os.path.dirname(__file__), "..", "momx", "scan.py"),
        encoding="utf-8",
    ).read()
    body = source[source.index("def scan_symbol"):]
    gates = body[: body.index("reasons: list[str] = []")]
    assert "completed_bars(" not in gates, (
        "a Price_Change gate is dropping the developing candle again - the "
        "trader's rule is that TOS reads the live one"
    )


def test_completed_bars_still_works_for_whoever_needs_it():
    """The helper stays: it is the documented escape hatch if the open-bell
    blackout returns, and deleting it would erase the 09:23 lesson."""
    tape = _bars([100.0, 100.0, 100.0])
    completed = scan.completed_bars(tape, 3600 // 60, tape[-1]["time"] + 60)
    assert len(completed) == len(tape) - 1


def test_a_falling_stock_is_still_blocked():
    """The fix must not turn the gate into a rubber stamp."""
    tape = _bars([320.0, 319.0, 318.0, 315.0])
    assert scan.price_change_gate(tape, "close", scan.CLOSE_CHANGE_MIN_PCT,
                                  scan.CLOSE_CHANGE_BARS_AGO) is False


def test_scan_symbol_no_longer_blocks_on_a_live_up_move():
    """End to end through the real gate wiring, not just the helper."""
    rising = _bars([315.87, 316.69, 316.56, 320.49])
    tapes = {"1h": rising, "4h": _bars([100.0] * 6), "D": rising}
    _, reasons = scan.scan_symbol(tapes, 320.49)
    assert "blocked:pricechange:close:1h" not in reasons, reasons
