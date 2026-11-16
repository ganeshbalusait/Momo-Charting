"""Strict headline -> ticker matching (news_names), the market-news feeds (news_live.media_items)
and the strict Google News source - no network."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

from catalyst_engine import GoogleNewsStrictSource
from news_live import media_items
from news_names import CompanyNames, is_junk, short_name

SEC = {str(i): row for i, row in enumerate([
    {"cik_str": 1, "ticker": "TGT", "title": "TARGET CORP"},
    {"cik_str": 2, "ticker": "DUOL", "title": "Duolingo, Inc."},
    {"cik_str": 3, "ticker": "QMCO", "title": "Quantum Corp"},
    {"cik_str": 4, "ticker": "SGML", "title": "Sigma Lithium Corp"},
    {"cik_str": 5, "ticker": "AAPL", "title": "Apple Inc."},
    {"cik_str": 6, "ticker": "INTC", "title": "INTEL CORP"},
    {"cik_str": 7, "ticker": "CAR", "title": "AVIS BUDGET GROUP, INC."},
    {"cik_str": 8, "ticker": "MSTR", "title": "Strategy Inc"},
    {"cik_str": 9, "ticker": "TSLA", "title": "Tesla, Inc."},
    {"cik_str": 10, "ticker": "HPE", "title": "Hewlett Packard Enterprise Co"},
])}
U = [row["ticker"] for row in SEC.values()]


def names():
    return CompanyNames(fetch=lambda: SEC)


def test_short_names():
    assert short_name("SIGMA LITHIUM CORP") == "sigma lithium"
    assert short_name("Hewlett Packard Enterprise Co") == "hewlett packard enterprise"
    assert short_name("AVIS BUDGET GROUP, INC.") == "avis budget"


def test_strict_matching_cases_seen_in_the_live_test():
    n = names()
    match = lambda h: n.tickers_in(h, U)  # noqa: E731
    assert match("Duolingo started at buy with $222 price target at Seaport") == ["DUOL"]        # not Target
    assert match("Better Quantum Computing Stock: IonQ vs. D-Wave") == []                       # not Quantum Corp
    assert match("Quantum Corp shares jump") == ["QMCO"]
    assert match("JPMorgan cuts Sigma Lithium stock rating on mining suspension") == ["SGML"]
    assert match("Target CEO says shoppers are pulling back") == ["TGT"]
    assert match("Apple's iPhone sales jump") == ["AAPL"] and match("Intel shares rise on foundry deal") == ["INTC"]
    assert match("Car prices keep rising") == [] and match("Avis Budget (CAR) jumps") == ["CAR"]
    assert match("ROSEN LAW FIRM ENCOURAGES CAR INVESTORS") == []                                # all caps
    assert match("IRS cracks down on an ETF strategy the wealthy use") == []                     # not Strategy Inc
    assert match("Hewlett Packard Enterprise stock hits all-time high") == ["HPE"]
    assert match("TSLA261002P00250000 Interactive Stock Chart") == []


def test_junk_titles():
    for title in ("Stocks making the biggest moves midday: Tesla, Broadcom", "Tesla earnings missed by $0.01, revenue topped",
                  "LYFT 8-K & SEC Filings", "The jobs report, Nike's China conundrum and more in Morning Squawk",
                  "Talen Energy Corp. Advanced Charts | TLN", "PL:VER Stock Latest News"):
        assert is_junk(title), title
    assert not is_junk("Tesla stock jumps 5% on deliveries")


def _rss(items):
    body = "".join(f"<item><title>{t}</title><link>https://x/{i}</link><pubDate>{format_datetime(d)}</pubDate>{extra}</item>"
                   for i, (t, d, extra) in enumerate(items))
    return f'<?xml version="1.0"?><rss><channel>{body}</channel></rss>'


def test_media_feed_keeps_recent_single_company_stories():
    now = datetime.now(timezone.utc)
    text = _rss([
        ("Tesla stock jumps 5% on better-than-expected deliveries", now - timedelta(hours=1), ""),
        ("Intel shares rise on foundry deal", now - timedelta(days=30), ""),                    # stale section item
        ("Tesla, Sigma Lithium, Duolingo and Hewlett Packard Enterprise all move", now, ""),  # names 4: round-up
        ("Markets rise on soft jobs data", now, ""),                                            # no company
    ])
    n = names()
    rows = media_items(text, "CNBC", lambda h: n.tickers_in(h, U), lambda h, s="": (0, "Neutral", []))
    assert [(r["symbol"], r["source"], r["via"]) for r in rows] == [("TSLA", "CNBC", "CNBC live")]


class _Engine:
    sec_user_agent = "test"


def test_google_news_strict_keeps_trusted_publishers_naming_the_company():
    now = datetime.now(timezone.utc)
    src = '<source url="https://x">{}</source>'
    text = _rss([
        ("JPMorgan cuts Sigma Lithium stock rating - Investing.com", now, src.format("Investing.com")),
        ("Sigma Lithium soars - Some Blog", now, src.format("Some Blog")),                     # untrusted
        ("Lithium prices slide - Reuters", now, src.format("Reuters")),                        # company not named
        ("SGML Stock Price, News, Quote - Yahoo Finance", now, src.format("Yahoo Finance")),  # quote page
    ])
    rows = GoogleNewsStrictSource(_Engine()).parse(text, "SGML", 25, names())
    assert [(r.headline, r.publisher) for r in rows] == [("JPMorgan cuts Sigma Lithium stock rating", "Investing.com")]


def test_law_firm_ads_and_ordinary_word_names_seen_after_going_live():
    assert is_junk("INVESTOR ALERT: Pomerantz Law Firm Reminds Investors with Losses on their Investment in Alibaba")
    assert is_junk("FCEL Shareholder Alert: Investors With Losses May Seek to Lead the Class Action")
    n = CompanyNames(fetch=lambda: {"0": {"cik_str": 1, "ticker": "BKNG", "title": "Booking Holdings Inc."}})
    assert n.tickers_in("From Booking to Follow-Through: How Armor Health Connects Intake Findings", ["BKNG"]) == []
    assert n.tickers_in("Booking shares fall after travel outlook cut", ["BKNG"]) == ["BKNG"]


# ---------------------------------------------------------------- a stale cache and a failed refetch (2026-11-14)
def _boom():
    raise OSError("SEC is not answering")


def test_a_stale_cache_is_still_used_when_the_refetch_fails(tmp_path):
    """The saved names list was thrown away once it was over a day old and the SEC refetch failed, so a headline
    naming the company was called "related news, not about LIDR" in the morning brief."""
    import json
    import os
    import time

    cache = tmp_path / "sec_company_tickers.json"
    cache.write_text(json.dumps({"0": {"ticker": "LIDR", "title": "AEye, Inc."}}), encoding="utf-8")
    old = time.time() - 3 * 86400
    os.utime(cache, (old, old))
    names = CompanyNames(cache, fetch=_boom).names()
    assert names.get("LIDR") == ["aeye"]
    assert cache.exists()                                   # the old copy is kept for the next try


def test_an_empty_load_is_tried_again_soon_not_a_day_later(tmp_path, monkeypatch):
    import news_names

    calls = []

    def fetch():
        calls.append(1)
        if len(calls) == 1:
            raise OSError("SEC is not answering")
        return {"0": {"ticker": "LIDR", "title": "AEye, Inc."}}

    table = CompanyNames(tmp_path / "none.json", fetch=fetch, aliases={"META": ["facebook"]})
    clock = [1000.0]
    monkeypatch.setattr(news_names.time, "monotonic", lambda: clock[0])
    assert "LIDR" not in table.names()                      # the fetch failed: aliases only
    clock[0] += 60
    assert "LIDR" not in table.names() and len(calls) == 1  # not hammered every call
    clock[0] += news_names.EMPTY_RETRY_SECONDS
    assert table.names().get("LIDR") == ["aeye"] and len(calls) == 2
