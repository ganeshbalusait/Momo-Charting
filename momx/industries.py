"""SYMBOL -> industry, a plain hand-edited table.

THIS FILE IS MEANT TO BE EDITED BY THE TRADER, BY HAND.
Add a symbol to the list next to its industry, save, restart the board. That
is the whole workflow. Keys are plain uppercase tickers and the values are the
industry labels exactly as they should appear in the board's "Industry"
column, so nothing needs to be decoded to read this file.

THERE IS DELIBERATELY NO SECTOR API DEPENDENCY. No lookup service, no vendor
classification feed, no scraping, no network call of any kind. Three reasons:

* The trader's groupings are not GICS. "ETF-Lev", "AI-Infra" and the way MSTR
  sits under Capital Markets are HIS buckets, taken off his own screenshots;
  a vendor taxonomy would silently overwrite them with something he did not
  ask for.
* A board column must never be able to fail, hang, or rate-limit. A dict
  cannot.
* This repository has a documented history of background data collectors
  starving the chart engine of CPU. A sector fetcher is exactly that shape.

An unmapped symbol returns None, which the payload contract already allows
(``"industry": null``). Unmapped is the normal state for most of a
400-symbol universe; it is not an error and nothing retries it.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# The table. Seeded from the trader's screenshots -- edit freely.
# ---------------------------------------------------------------------------

INDUSTRY_SYMBOLS: dict[str, tuple[str, ...]] = {
    "Semis": ("NVDA", "INTC", "MU", "AVGO", "TSEM", "AMKR", "ARM", "NVTS",
              "MRVL", "COHR", "WOLF"),
    "Software": ("MSFT", "OKTA", "CRM", "RBRK", "S", "PLTR",
                 "SNPS", "TEAM", "PATH", "APPS"),
    "Apparel": ("ANF", "AEO", "GAP", "URBN", "BBWI"),
    "Networking": ("LITE", "GLW"),
    "Tech-HW": ("WDC", "STX", "AAPL"),
    "ETF-Lev": ("NVDL", "SOXL", "KORU", "NVDX", "MSTU", "MSTX", "TSLL", "CONL"),
    "AI-Infra": (),
    "Biotech": ("SMMT", "MRNA"),
    "Solar": ("SEDG",),
    "Utilities": ("NNE",),
    "Capital Markets": ("COIN", "SBET", "MSTR"),
    "IT Services": ("AI",),
    # The AI and security themes from the trader's "Sector ETFs Top Holdings"
    # Google Sheet (2026-09-21). Only these names were taken from it: the sheet
    # lists what each ETF HOLDS, so a mega-cap appears under up to 7 funds
    # (AVGO x6, PLTR x7) and its broad/index labels ("Russell 2000",
    # "Technology Autonomous" for AMZN) are worse than the hand labels here.
    "AI - Data Centers": ("CRWV", "NBIS", "APLD", "SMCI", "DELL", "HPE", "VRT",
                          "ORCL"),
    "AI - Networking": ("ALAB", "CRDO", "ANET", "CIEN", "CLS", "RMBS"),
    "AI - Power": ("CEG", "VST", "TLN", "GEV"),
    "AI - Cloud": ("SNOW", "MDB", "DDOG"),
    "Cybersecurity": ("CRWD", "PANW", "ZS", "NET"),
    # The trader's 001_Mega7 watchlist. Labels kept in the short MomoX style
    # used above; thinkorswim shows the long GICS names for the same names
    # ("Broadline Retail", "Interactive Media & Services", ...).
    "Retail": ("AMZN",),
    "Interactive Media": ("GOOGL", "META"),
    "Entertainment": ("NFLX",),
    "Autos": ("TSLA",),
    "Commodity ETF": ("USO", "GLD", "SLV"),
    "Index ETF": ("SPY", "QQQ", "SPX", "IWM", "DIA"),
}

# ---------------------------------------------------------------------------
# Full-watchlist coverage, added 2026-08-27 ("Why most of the tickers industry
# is missing? fix it"). The map above was seeded only from the trader's MomoX
# screenshots (~58 names); everything else in the 358-symbol watchlist rendered
# a blank Industry cell. Labels stay in the short MomoX style, hand-editable.
#
# NOT hand-mapped: NASA (issuer unidentified). Anything absent from this map
# is looked up live via momx.industry_lookup (Finnhub/FMP) and cached, so a
# blank cell now means BOTH the map and the providers had nothing.
# ---------------------------------------------------------------------------
INDUSTRY_SYMBOLS.update({
    "E-Commerce": ("BABA", "CART", "CHWY", "CVNA", "DASH", "EBAY", "JD", "PDD", "SHOP"),
    "Travel": ("ABNB", "BKNG", "CCL", "HLT", "MAR", "NCLH"),
    "Airlines": ("AAL", "DAL", "LUV", "UAL"),
    "Restaurants": ("BROS", "CAVA", "MCD", "SBUX"),
    "Beverages": ("BUD", "CELH", "KO", "PEP"),
    "Food": ("BYND", "HSY", "MDLZ"),
    "Consumer": ("KVUE", "PG"),
    "Tobacco": ("MO", "PM"),
    "Cannabis": ("ACB", "MSOS", "TLRY"),
    "Pharma": ("ABBV", "BMY", "JNJ", "LLY", "MRK", "PFE"),
    "Healthcare": ("CVS", "HIMS", "NTRA", "TEM", "TMO", "UNH"),
    "Banks": ("BAC", "GS", "HSBC", "JPM", "PNC", "USB", "WFC"),
    "Fintech": ("AFRM", "AXP", "LMND", "MA", "PYPL", "SOFI", "TOST", "UPST", "XYZ"),
    "Oil & Gas": ("CNQ", "COP", "CVX", "ET", "HAL", "MPC", "OXY", "PSX", "SHEL",
                  "SLB", "XOM"),
    "Metals": ("AA", "CLF", "CRML", "FCX", "HL", "LAC", "MP", "NAK", "PAAS",
               "SCCO", "SGML", "TMC", "UAMY", "USAR"),
    "Gold": ("AEM", "KGC", "NEM"),
    "Uranium": ("CCJ", "UEC", "UUUU"),
    "Nuclear": ("OKLO", "SMR"),
    "Clean Energy": ("BE", "BLDP", "EOSE", "FCEL", "PLUG"),
    "Crypto": ("BMNR", "BTBT", "CIFR", "CLSK", "CRCL", "GLXY", "HIVE", "HUT",
               "IBIT", "IREN", "MARA", "RIOT", "WULF"),
    # SPCX is Space Exploration Technologies (SpaceX), verified via Finnhub -
    # NOT the retired SPAC ETF of the same ticker.
    "Space": ("ASTS", "FLY", "IRDM", "LUNR", "PL", "RDW", "RKLB", "SPCE", "SPCX"),
    "Quantum": ("ARQQ", "INFQ", "IONQ", "QBTS", "QUBT", "RGTI"),
    "Defense": ("AVAV", "AXON", "BA", "LHX", "LMT", "NOC", "RTX"),
    "Air Mobility": ("ACHR", "JOBY"),
    "Drones": ("ONDS", "RCAT", "UMAC"),
    "Robotics": ("KITT", "RR", "SYM"),
    "EV": ("ENVX", "LCID", "NIO", "QS", "RIVN", "XPEV"),
    "Auto-Tech": ("AEVA", "LIDR", "MVIS", "OUST", "PONY"),
    "Transport": ("CAR", "FDX", "GRAB", "HTZ", "LYFT", "UBER", "UPS"),
    "Telecom": ("CHTR", "CMCSA", "TMUS", "VZ"),
    "Gaming": ("DKNG", "LVS", "PENN", "RBLX", "TTWO"),
    "Industrials": ("CAT", "ECL", "GE", "QXO"),
    "Homebuilders": ("DHI", "PHM"),
    "Real Estate": ("OPEN", "WELL"),
    "Insurance": (),
    "ETF": ("CHPY", "MSTY", "XOVR"),
    "Fund": ("VCX",),  # Fundrise Innovation Fund (per Finnhub profile)
})

# Additions to categories that already exist above (a dict literal cannot
# repeat a key, so these extend in place).
for _industry, _adds in {
    "Semis": ("AMD", "AMAT", "ASML", "DRAM", "GFS", "LAES", "LRCX",
              "MCHP", "POET", "QCOM", "SNDK", "STM", "TSM", "TXN"),
    "Software": ("ADBE", "APP", "BB", "BBAI", "DOCU", "FSLY", "INOD",
                 "IOT", "NOW", "SOUN", "WDAY", "ZETA", "ZM"),
    "Tech-HW": ("HPQ", "LASR", "QMCO"),
    "Networking": ("AAOI", "CSCO", "NOK"),
    "AI-Infra": ("TSSI",),
    "IT Services": ("IBM", "INFY"),
    "Capital Markets": ("BULL", "CBOE", "CME", "FUTU", "HOOD", "IBKR", "MS",
                        "SCHW", "SPGI"),
    "Utilities": ("NEE",),
    "Biotech": ("ABVX", "AMGN", "BEAM", "GILD", "INSM", "MNKD", "NVAX", "REGN",
                "RXRX", "VKTX"),
    "Solar": ("ENPH", "FSLR", "RUN"),
    "Apparel": ("DECK", "NKE", "ONON"),
    "Autos": ("F", "GM"),
    "Retail": ("AAP", "COST", "DG", "DLTR", "GME", "HD", "KR", "KSS", "LOW", "M",
               "ROST", "TGT", "TJX", "ULTA", "WMT", "WRBY"),
    "Entertainment": ("AMC", "DIS", "FUBO", "ROKU", "SPOT", "TME", "WBD"),
    "Interactive Media": ("BIDU", "BILI", "DJT", "GOOG", "PINS", "RDDT", "RUM",
                          "SNAP", "WB"),
    "Index ETF": ("SCHD",),
    "ETF-Lev": ("BOIL", "NAIL", "NASA", "SOXS", "TQQQ"),  # NASA: 3x ETF, per the trader
}.items():
    INDUSTRY_SYMBOLS[_industry] = tuple(INDUSTRY_SYMBOLS[_industry]) + _adds

# Empty placeholder keys must not become dropdown entries.
INDUSTRY_SYMBOLS = {k: v for k, v in INDUSTRY_SYMBOLS.items() if v}


#: Flattened SYMBOL -> industry. Built from :data:`INDUSTRY_SYMBOLS` so the
#: editable table above stays the single place to change. If one symbol is
#: listed under two industries the LAST industry in the table wins; the
#: duplicate is not an error because the trader may deliberately move a name
#: (MSTR has lived under both Capital Markets and Software on his screens).
INDUSTRY_MAP: dict[str, str] = {
    symbol.strip().upper(): industry
    for industry, symbols in INDUSTRY_SYMBOLS.items()
    for symbol in symbols
    if str(symbol or "").strip()
}


def industry_for(symbol: object) -> str | None:
    """The industry label for ``symbol``, case-insensitively, else None."""
    if symbol is None:
        return None
    key = str(symbol).strip().upper()
    if not key:
        return None
    return INDUSTRY_MAP.get(key)


def industries() -> list[str]:
    """Every industry label in table order, for a filter dropdown."""
    return list(INDUSTRY_SYMBOLS)


__all__ = ["INDUSTRY_MAP", "INDUSTRY_SYMBOLS", "industries", "industry_for"]
