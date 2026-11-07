"""Symbol forms and the screening universe for the Long Term screen.

One canonical form everywhere inside the app (``BRK-B``); each outside source
gets its own spelling at the edge: Schwab wants ``BRK/B``, Finnhub ``BRK.B``,
SEC EDGAR's ticker file ``BRK-B``.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

_VALID = re.compile(r"^[A-Z0-9][A-Z0-9-]{0,9}$")
_BASE_DIR = Path(__file__).resolve().parent.parent

# The trader's Google Sheet of tickers to screen, copied 2026-11-06.
# Sheet id 1YmDZqMJUSSSQxJC-BkOYBHtY22ALUAmctO8q3XNcLJc (239 distinct tickers;
# the 241 raw rows had two duplicates). GOOG and GOOGL are distinct tickers.
SEED_TICKERS: tuple[str, ...] = tuple(
    """
    AA AAL AAPL ABBV ABNB ACHR ADBE AEM AEVA AFRM AI AMC AMD AMGN AMZN ANET ANF
    APLD ARM ASML ASTS AVAV AVGO AXON BA BABA BAC BBWI BE BIDU BILI BITF BKNG
    BMNR BMY BYND C CAT CAVA CCJ CCL CELH CHTR CHWY CIFR CLF CLSK CMCSA COIN COP
    COST CRCL CRDO CRM CRML CRWD CRWV CSCO CVNA CVS CVX DAL DASH DDOG DELL DG
    DHI DIA DIS DJT DKNG DLTR EA ECL ENPH EOSE ET F FCX FDX FSLR FUBO FUTU GE
    GEV GILD GLXY GME GOOG GOOGL GS HD HIMS HIVE HL HOOD HUT IBM INFY INSM INTC
    IONQ IRDM IREN JD JNJ JOBY JPM KGC KO KR KSS LAC LCID LHX LLY LMND LOW LRCX
    LUNR LUV LYFT M MAR MARA MCD MCHP MDB META MO MRK MRNA MRVL MS MSFT MSTR MU
    NCLH NEE NEM NFLX NIO NKE NOW NTRA NVAX NVDA OKLO OKTA ONDS OPEN ORCL OXY
    PANW PATH PDD PENN PEP PFE PG PHM PINS PL PLTR PLUG PMH PYPL QBTS QCOM QQQ QS
    QUBT RBLX RCAT RDDT RDW REGN RGTI RIOT RIVN RKLB ROKU ROST RUM RUN RXRX SBET
    SBUX SGML SHOP SLV SMCI SMR SNAP SNDK SNOW SOFI SPGI SPY STX T TEAM TEM TGT
    TLN TME TMUS TOST TSLA TSM TTWO TXN U UAL UAMY UBER UEC UNH UPS UPST USB UUUU
    V VKTX VRT VZ W WB WBD WDC WFC WMT WULF XOM XPEV XYZ ZM ZS AAOI
    """.split()
)

# Ticker -> SEC CIK, applied before company_tickers.json (R20). company_tickers.json maps XOM to the
# new ExxonMobil Holdings CIK 2115436, which carries only the 2026 10-Qs; the 2008-2025 10-Ks sit under
# 34088, so 34088 is fetched first and 2115436 is merged in (edgar.EXTRA_CIKS / merge_companyfacts, R42).
CIK_OVERRIDES: dict[str, int] = {"XOM": 34088}


def canonical(symbol: Any) -> str:
    """Upper-case, trimmed, ``.`` and ``/`` -> ``-``; "" for anything unusable."""
    if symbol is None or isinstance(symbol, bool):
        return ""
    text = str(symbol).strip().upper().replace(".", "-").replace("/", "-")
    return text if _VALID.match(text) else ""


def to_schwab(symbol: Any) -> str:
    return canonical(symbol).replace("-", "/")


def to_finnhub(symbol: Any) -> str:
    return canonical(symbol).replace("-", ".")


def to_edgar(symbol: Any) -> str:
    return canonical(symbol)


def _read_watchlist(path: Path | None) -> list[str]:
    """Same format as ``config._load_watchlist``: symbols split on whitespace or
    commas; ``#`` starts a comment. Missing or unreadable file -> []."""
    target = path if path is not None else Path(os.getenv("WATCHLIST_FILE", _BASE_DIR / "watchlist.txt"))
    try:
        text = Path(target).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return []
    out: list[str] = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].replace(",", " ")
        out.extend(line.split())
    return out


def universe(*, watchlist_path: Path | None = None, universe_path: Path | None = None) -> list[str]:
    """Sorted canonical union of the watchlist file, every MomX list and the seed."""
    raw: list[Any] = list(_read_watchlist(watchlist_path))
    try:
        from momx.board import load_universes

        for symbols in load_universes(path=universe_path).values():
            raw.extend(symbols)
    except Exception:  # noqa: BLE001 - a broken MomX file must not empty the screen
        pass
    raw.extend(SEED_TICKERS)
    return sorted({s for s in (canonical(x) for x in raw) if s})
