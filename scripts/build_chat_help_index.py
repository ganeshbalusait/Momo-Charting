"""Generates momx/chat_help_index.json, the app's own words for the Ask chat's search (2026-11-11).

    .venv/Scripts/python.exe scripts/build_chat_help_index.py            write the file
    .venv/Scripts/python.exe scripts/build_chat_help_index.py --check    exit 1 when the file is out of date

Sources: frontend/src/learnContent.js and strategyRules in momxFilters.js (run with node, since both are
plain modules), the pages and columns lifted from App.jsx and MomxScannerPanel.jsx, and momx/rule_help.json.
Every document is marked "all" or "owner". Anything naming the owner's pages is "owner", whichever source it
came from, and momx/chat_rag_index.py applies the same check again when it builds the search files.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from momx.chat_rag_index import OWNER_ONLY, PERSON  # noqa: E402

OUT = ROOT / "momx" / "chat_help_index.json"
SRC = ROOT / "frontend" / "src"
LEARN = {"label": "Open Learn + Setup", "view": "Learn + Setup"}
SCANNER = {"label": "Open MomX Scanner", "view": "MomX Scanner"}
BOT = {"label": "Open Bot", "view": "Bot"}

#: What each scanner column means, in plain words. A test holds this to MOMX_COLUMNS.
COLUMN_MEANINGS = {
    "Industry": "The ticker's industry group.",
    "Symbol": "The ticker. Tap it to see it on the chart.",
    "Time": "On History, when this row was saved. A row is saved only when a column changes, so saved rows are not every scan.",
    "Setup": "The scanner's grade for the ticker and the setup tags it has right now.",
    "Fresh": "What changed for this ticker in the last 15 minutes.",
    "% Chg": "How far the stock has moved today, as a percent.",
    "Price": "The stock's current price.",
    "Spread": "How wide the stock's option prices are: Tight, OK, Wide or Avoid. A wide spread costs more to get in and out.",
    "News": "How old the newest headline is. A sparkle means the AI reader judged it, and its colour says which way.",
    "Record": "How this kind of setup has done in the app's own history. It looks back and is not a forecast.",
    "Event": "The next earnings date, when the ticker is tracked.",
    "1D": "The shape of the day, as a small line.",
    "RVOL": "Relative volume on each timeframe: how much is trading compared with what is normal for this stock at this time of day. Above one means busier than usual.",
    "H/L": "High/Low: where the price sits inside the last 8 two-hour bars, drawn as a bar. Near +1 is the top of that range and near -1 the bottom. The board is ranked on it.",
    "COLOR": "A thin coloured separator between the RVOL and SQZ blocks. It carries no number.",
    "SQZ": "Squeeze on each timeframe: whether the range has tightened, and when the squeeze fired.",
    "SKIT": "Skittles on each timeframe: a fast momentum reading between 0 and 100, coloured by which way the trend is running.",
    "Quote": "The quote trend: the last few bars up or down as a tiny bar chart.",
}
#: Other words people use for a page. The same words as momx/chat_pages.py, so a search finds what a link finds.
PAGE_ALIASES = {
    "Charts & OI": "chart charts levels", "Quick Options": "options option chain high oi walls", "Auto Alert": "alerts oi alerts",
    "Premarket Scanner": "premarket pre market", "MomX Scanner": "scanner momx mag7 board watchlist board history find filters",
    "Long Term": "buffett value long term", "Learn + Setup": "learn help how to", "Watchlist": "my tickers", "Sectors": "heatmap sector",
    "News Feed": "news headlines", "Earnings Calendar": "earnings", "Mag7 Scanner": "mag7 watchlist", "ROI Calc": "roi",
    "OI Level Script TOS": "tos script oi script", "Release Notes": "what changed what's new", "Settings": "password account",
    "Bot": "bot desk scorecard ask tab", "Option Paper Trading": "option paper", "Paper Trading": "stock paper paper",
}
_NODE = """
const L = await import(process.argv[1] + "/learnContent.js");
const F = await import(process.argv[1] + "/momxFilters.js");
process.stdout.write(JSON.stringify({steps: L.LEARN_STEPS, sections: L.LEARN_SECTIONS, setup: L.LEARN_SETUP_SECTIONS, glossary: L.LEARN_GLOSSARY,
  elsewhere: L.LEARN_ELSEWHERE, labels: F.STRATEGY_LABELS, bull: F.strategyRules("bull"), bear: F.strategyRules("bear")}));
"""


def _node_data() -> dict:
    url = SRC.as_uri()
    done = subprocess.run(["node", "--input-type=module", "-e", _NODE, url], capture_output=True, text=True, encoding="utf-8", check=True, cwd=SRC, timeout=120)
    return json.loads(done.stdout)


def pages() -> list[dict]:
    """Every page in the left rail, with whether it is the owner's."""
    source = (SRC / "App.jsx").read_text(encoding="utf-8")
    block = source[source.index("const optionNavItems = ["):]
    block = block[:block.index("\n];")]
    described = dict(re.findall(r'if \(view === "([^"]+)"\) return "([^"]+)"', source))
    out = []
    for line in block.splitlines():
        m = re.match(r'\s*\{ label: "([^"]+)"(.*)\}', line)
        if m:
            shown = re.search(r'displayLabel: "([^"]+)"', m.group(2))
            out.append({"view": m.group(1), "label": shown.group(1) if shown else m.group(1), "adminOnly": "adminOnly: true" in m.group(2), "text": described.get(m.group(1), "")})
    return out


def column_labels() -> list[str]:
    """The labels the scanner shows: each plain column, and each group once."""
    source = (SRC / "MomxScannerPanel.jsx").read_text(encoding="utf-8")
    block = source[source.index("const MOMX_COLUMNS = ["):]
    block = block[:block.index("\n];")]
    labels: list[str] = []
    for line in block.splitlines():
        m = re.search(r'\blabel: "([^"]+)"(?:, group: "([^"]+)")?', line)
        if m:
            name = {"RVOL": "RVOL", "SQZ": "SQZ", "SKIT": "SKIT"}.get(m.group(2) or "", m.group(1))
            if m.group(2) == "CHART":
                name = m.group(1)
            if name not in labels:
                labels.append(name)
    return labels


def _doc(source: str, title: str, text: str, link: dict, owner: bool = False) -> dict:
    text = " ".join(str(text).split())
    title = " ".join(str(title).split())
    private = owner or bool(OWNER_ONLY.search(f"{title} {text}"))
    return {"source": source, "title": title, "text": text, "scope": "owner" if private else "all", "link": link}


def _learn_docs(section: dict, source: str, link: dict) -> list[dict]:
    docs = [_doc(source, section["title"], " ".join(section["concept"]), link)]
    for block in section.get("blocks", []):
        rows = " ".join(f"{row['term']}: {row['meaning']}" for row in block.get("rows", []))
        chips = " ".join(chip["label"] for chip in block.get("chips", []))
        docs.append(_doc(source, f"{section['title']}: {block['heading']}", " ".join([*block.get("body", []), rows, chips]), link))
    return docs


def documents() -> list[dict]:
    data = _node_data()
    docs: list[dict] = []
    for page in pages():
        link = {"label": f"Open {page['label']}", "view": page["view"]}
        also = PAGE_ALIASES.get(page["view"], "")
        docs.append(_doc("page", f"{page['label']} page", f"{page['text']}. Also called: {also}.".lstrip(". "), link, page["adminOnly"]))
    for item in data["glossary"]:
        docs.append(_doc("glossary", item["term"], item["plain"], LEARN))
    for step in data["steps"]:
        docs.append(_doc("learn", step["title"], " ".join(step["body"]), LEARN))
    for section in data["sections"]:
        docs += _learn_docs(section, "learn", LEARN)
    for section in data["setup"]:
        docs += _learn_docs(section, "setup", LEARN)
    for item in data["elsewhere"]:
        docs.append(_doc("page", f"{item['goToLabel']} page", item["oneLiner"], {"label": f"Open {item['goToLabel']}", "view": item["goTo"]}))
    for label in column_labels():
        docs.append(_doc("column", f"{label} column", COLUMN_MEANINGS[label], SCANNER))
    for side, word in (("bull", "calls"), ("bear", "puts")):
        for key, text in data[side].items():
            if side == "bear" and data["bull"].get(key) == text:
                continue                              # the wording is the same on both sides
            docs.append(_doc("rule", f"{data['labels'].get(key, key)} ({word})", text, SCANNER))
    rules = json.loads((ROOT / "momx" / "rule_help.json").read_text(encoding="utf-8"))
    for key, item in rules.items():                   # the Bot page's rules
        docs.append(_doc("rule", key, f"{item.get('what', '')} {item.get('needs', '')}", BOT, owner=True))
    return [doc for doc in docs if doc["text"] and not PERSON.search(f"{doc['title']} {doc['text']}")]


def render() -> str:
    return json.dumps({"note": "Generated by scripts/build_chat_help_index.py. Do not edit by hand.", "docs": documents()}, ensure_ascii=False, indent=1) + "\n"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args(argv)
    text = render()
    path = Path(args.out)
    if args.check:
        same = path.exists() and path.read_text(encoding="utf-8") == text
        print("up to date" if same else "out of date: run scripts/build_chat_help_index.py")
        return 0 if same else 1
    path.write_bytes(text.encode("utf-8"))
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
