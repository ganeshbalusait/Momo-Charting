"""Copy the Sectors page's cards into momx/sector_cards.json for the chat.

The Sectors page keeps its sector cards in frontend/src/sectorHeatmap.js
(SECTOR_CARDS). The chat runs in Python and must say the same thing about which
sector a ticker sits in, so this script reads the JS source and writes a plain
JSON copy. It reads and writes local files only and has no clock in it, so the
same source always gives the same bytes.

    .venv/Scripts/python.exe scripts/build_chat_sector_cards.py          # write
    .venv/Scripts/python.exe scripts/build_chat_sector_cards.py --check  # exit 1 if out of date

tests/test_momx_chat_market.py compares the JSON with what node reads from the
JS module, so a card edited on the page without a rebuild fails the suite.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "frontend" / "src" / "sectorHeatmap.js"
TARGET = ROOT / "momx" / "sector_cards.json"

_LINE = re.compile(r'^\s*\{\s*name:\s*"', re.MULTILINE)


def _field(line: str, key: str) -> str:
    found = re.search(rf'\b{key}:\s*"((?:[^"\\]|\\.)*)"', line)
    if not found:
        raise ValueError(f"sector card is missing {key}: {line[:80]}")
    return found.group(1)


def _members(text: str) -> list[dict]:
    out = []
    for pair in text.split():
        symbol, cap_billions = pair.split(":")
        out.append({"symbol": symbol, "capB": float(cap_billions)})
    return out


def _groups(text: str) -> list[dict]:
    out = []
    for part in filter(None, text.split("|")):
        label, symbols = part.split("=")
        out.append({"label": label, "symbols": symbols.split()})
    return out


def parse_cards(source: str) -> list[dict]:
    """The SECTOR_CARDS entries of sectorHeatmap.js, in page order."""
    start = source.index("export const SECTOR_CARDS = [")
    end = source.index("].map(", start)
    body = source[start:end]
    cards = []
    for line in body.splitlines():
        if not _LINE.match(line):
            continue
        cards.append({
            "name": _field(line, "name"),
            "etf": _field(line, "etf"),
            "about": _field(line, "about"),
            "leaders": _members(_field(line, "leaders")),
            "more": _members(_field(line, "more")),
            "groups": _groups(_field(line, "groups")),
        })
    if not cards:
        raise ValueError("no sector cards found in sectorHeatmap.js")
    return cards


def render(source: str) -> str:
    document = {"source": "frontend/src/sectorHeatmap.js", "cards": parse_cards(source)}
    return json.dumps(document, indent=1, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> int:
    text = render(SOURCE.read_text(encoding="utf-8"))
    if "--check" in argv:
        held = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if held != text:
            print("momx/sector_cards.json is out of date: run scripts/build_chat_sector_cards.py")
            return 1
        print("momx/sector_cards.json matches the Sectors page")
        return 0
    TARGET.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {TARGET} ({len(json.loads(text)['cards'])} cards)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
