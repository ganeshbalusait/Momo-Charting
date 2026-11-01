"""news_live.py - stories pushed into the headline store as they are published (no network)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from news_live import LiveNewsIngest, alpaca_items, edgar_items, wire_items


def score(headline, summary=""):
    return (1, "Positive", ["Contract"]) if "contract" in headline.lower() else (0, "Neutral", [])


def test_alpaca_message_becomes_one_row_per_ticker():
    msg = {"T": "n", "id": 42, "headline": "Acme wins <b>$2B</b> Army contract", "summary": "Big deal",
           "created_at": "2026-10-02T14:31:05Z", "url": "https://www.benzinga.com/x", "symbols": ["ACME", "lmt"],
           "source": "benzinga"}
    rows = alpaca_items(msg, score)
    assert [r["symbol"] for r in rows] == ["ACME", "LMT"]
    r = rows[0]
    assert r["headline"] == "Acme wins $2B Army contract" and r["source"] == "Benzinga" and r["via"] == "Alpaca live"
    assert r["published_at"] == "2026-10-02T14:31:05+00:00" and r["sentiment"] == "Positive"
    assert r["related_symbols"] == "ACME,LMT" and r["article_id"] == "42"
    assert alpaca_items({"T": "subscription"}, score) == []
    assert alpaca_items({**msg, "symbols": []}, score) == []                  # untagged: nowhere to file it
    ad = {**msg, "headline": "ACME Investors Have Opportunity to Lead Acme Securities Fraud Lawsuit"}
    assert alpaca_items(ad, score) == []                                       # law-firm ad, not news


WIRE = """<?xml version="1.0"?><rss><channel>
<item><title>Acme Signs Supply Contract</title><link>https://www.prnewswire.com/a</link>
<pubDate>Fri, 2 Oct 2026 14:30:00 +0000</pubDate><description>NEW YORK -- Acme Corp. (NASDAQ: ACME; TSX: ACM) today...</description></item>
<item><title>Tiny Co update</title><link>https://www.prnewswire.com/b</link>
<pubDate>Fri, 2 Oct 2026 14:30:00 +0000</pubDate><description>Tiny Co (OTCQB: TINY) today...</description></item>
<item><title>ROSEN LAW: Acme Investors Who Lost Money Encouraged to Act</title><link>https://www.prnewswire.com/c</link>
<pubDate>Fri, 2 Oct 2026 14:30:00 +0000</pubDate><description>(NASDAQ: ACME)</description></item>
<item><title>Bank news</title><link>https://www.prnewswire.com/d</link>
<pubDate>Fri, 2 Oct 2026 14:31:00 +0000</pubDate><description>Big Bank (NYSE: BB) and Other (NYSE American: OTH)</description></item>
</channel></rss>"""


def test_wire_release_kept_only_with_a_listed_ticker():
    rows = wire_items(WIRE, "PR Newswire", score)
    assert [(r["symbol"], r["url"][-1]) for r in rows] == [("ACME", "a"), ("BB", "d"), ("OTH", "d")]
    assert rows[0]["via"] == "PR Newswire live" and "Press release" in rows[0]["tags"]
    assert wire_items("not xml", "PR Newswire", score) == []


EDGAR = """<?xml version="1.0" encoding="ISO-8859-1" ?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>4 - Thompson Donald (0001374074) (Reporting)</title>
<link rel="alternate" href="https://www.sec.gov/Archives/edgar/data/1374074/000137407426000012/0001374074-26-000012-index.htm"/>
<summary type="html">&lt;b&gt;Filed:&lt;/b&gt; 2026-10-02 &lt;b&gt;AccNo:&lt;/b&gt; 0001374074-26-000012</summary>
<updated>2026-10-02T18:06:51-04:00</updated><id>urn:tag:sec.gov,2008:accession-number=0001374074-26-000012</id></entry>
<entry><title>8-K - Alphabet Inc. (0001652044) (Filer)</title>
<link rel="alternate" href="https://www.sec.gov/Archives/edgar/data/1652044/000165204426000099/0001652044-26-000099-index.htm"/>
<summary type="html">&lt;b&gt;Filed:&lt;/b&gt; 2026-10-02 &lt;b&gt;AccNo:&lt;/b&gt; 0001652044-26-000099</summary>
<updated>2026-10-02T16:05:00-04:00</updated><id>urn:tag:sec.gov,2008:accession-number=0001652044-26-000099</id></entry>
<entry><title>D - Private Fund LP (0009999999) (Filer)</title>
<link rel="alternate" href="https://www.sec.gov/x"/><updated>2026-10-02T16:00:00-04:00</updated></entry>
</feed>"""


def test_edgar_latest_filings_map_cik_to_ticker():
    rows, oldest = edgar_items(EDGAR, {"1652044": ["GOOGL", "GOOG"], "1374074": ["NOPE"]}, score)
    assert [r["symbol"] for r in rows] == ["GOOGL", "GOOG"]                    # insider row and Form D dropped
    assert rows[0]["headline"].startswith("8-K filing: Alphabet Inc. - Current report (2026-10-02, AccNo 0001652044-26-000099)")
    assert rows[0]["via"] == "SEC EDGAR live" and rows[0]["source"] == "SEC EDGAR" and "Filing" in rows[0]["tags"]
    assert oldest == datetime(2026, 10, 2, 20, 0, tzinfo=timezone.utc)


class Repo:
    def __init__(self):
        self.calls = []

    def log_catalysts(self, items):
        self.calls.append(items)
        return len(items)


def _row(symbol, url, minutes_old=1):
    published = datetime.now(timezone.utc) - timedelta(minutes=minutes_old)
    return {"symbol": symbol, "url": url, "headline": "h", "published_at": published.isoformat()}


def test_store_skips_repeats_and_off_list_tickers_and_times_arrival():
    repo = Repo()
    live = LiveNewsIngest(repo, score, lambda: [], universe=lambda: ["ACME", "BB"])
    assert live.store("wires", [_row("ACME", "u1", 90), _row("ZZZ", "u2")]) == 1   # first poll = backlog
    assert live.store("wires", [_row("ACME", "u1", 90), _row("BB", "u3", 2)]) == 1   # u1 already delivered
    assert [r["symbol"] for call in repo.calls for r in call] == ["ACME", "BB"]
    status = live.status()["wires"]
    assert status["received"] == 2 and status["stored"] == 2
    assert 100 <= status["medianLagSec"] <= 140                                      # only the warm poll is timed
    assert not any(k.startswith("_") for k in status)
    everyone = LiveNewsIngest(Repo(), score, lambda: [])                             # no universe yet: keep all
    assert everyone.store("alpaca", [_row("ZZZ", "u9")]) == 1


def test_each_wire_site_has_its_own_backlog():
    live = LiveNewsIngest(Repo(), score, lambda: [])
    live.store("wires", [_row("ACME", "a1", 1)], part="PR Newswire")
    live.store("wires", [_row("BB", "g1", 300)], part="GlobeNewswire")      # GNW's first read: backlog
    live.store("wires", [_row("CC", "g2", 2)], part="GlobeNewswire")
    assert live.status()["wires"]["medianLagSec"] < 200
