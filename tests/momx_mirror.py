"""Mirror helpers for the BEAR scanner tests.

A bear tape is a bull tape reflected in price: ``p' = 2*PIVOT - p``, high and
low swapped, volume kept. EMAs, MACD and the squeeze midline are linear in
price and the Bollinger/Keltner widths are invariant, so every bull cross or
squeeze fire on the original is the same BEAR cross or fire on the mirror.

Colour mirrors follow momx/columns.py's own ladders (cyan<->magenta,
green<->red, lime<->light_red, dark_green<->plum, H/L green<->red).
"""
from __future__ import annotations

PIVOT = 1000.0

SKIT_BG = {"cyan": "magenta", "green": "red", "lime": "light_red", "dark_green": "plum",
           "magenta": "cyan", "red": "green", "light_red": "lime", "plum": "dark_green"}
SKIT_FG = {"cyan": "magenta", "dark_green": "plum", "downtick": "violet",
           "magenta": "cyan", "plum": "dark_green", "violet": "downtick"}
RVOL_BG = {"cyan": "magenta", "green": "red", "magenta": "cyan", "red": "green"}
RVOL_FG = {"cyan": "magenta", "green": "red", "dark_green": "dark_red",
           "magenta": "cyan", "red": "green", "dark_red": "dark_green"}
SQZ_BG = {"cyan": "magenta", "magenta": "cyan"}
HL_BG = {"green": "red", "red": "green"}


def mirror_price(value, pivot: float = PIVOT):
    return None if value is None else 2 * pivot - float(value)


def mirror_bars(bars, pivot: float = PIVOT) -> list[dict]:
    out = []
    for b in bars:
        c = dict(b)
        c["open"] = 2 * pivot - float(b["open"])
        c["close"] = 2 * pivot - float(b["close"])
        c["high"] = 2 * pivot - float(b["low"])
        c["low"] = 2 * pivot - float(b["high"])
        out.append(c)
    return out


def mirror_tapes(tapes: dict, pivot: float = PIVOT) -> dict:
    return {key: mirror_bars(bars, pivot) for key, bars in tapes.items()}


def _cell(cell, bg_map: dict, fg_map: dict):
    if not isinstance(cell, dict):
        return cell
    c = dict(cell)
    if "bg" in c:
        c["bg"] = bg_map.get(c["bg"], c["bg"])
    if "fg" in c:
        c["fg"] = fg_map.get(c["fg"], c["fg"])
    return c


def mirror_row(row: dict) -> dict:
    """The bear twin of a graded board row (colours only; prices untouched)."""
    r = dict(row)
    for section, bg_map, fg_map in (
        ("skittles", SKIT_BG, SKIT_FG), ("rvol", RVOL_BG, RVOL_FG), ("sqz", SQZ_BG, {})
    ):
        part = row.get(section)
        if isinstance(part, dict):
            r[section] = {tf: _cell(cell, bg_map, fg_map) for tf, cell in part.items()}
    if isinstance(row.get("highLow"), dict):
        r["highLow"] = _cell(row["highLow"], HL_BG, {})
    return r
