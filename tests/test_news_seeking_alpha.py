"""Seeking Alpha per-ticker RSS source (catalyst_engine.SeekingAlphaSource), no network."""
from __future__ import annotations

from catalyst_engine import DEFAULT_SOURCES, SOURCE_CLASSES, SeekingAlphaSource

FEED = """<?xml version="1.0"?>
<rss xmlns:sa="https://seekingalpha.com/api/1.0" version="2.0"><channel>
<item><title>HPE climbs 7% to new 52-week high</title>
<link>https://seekingalpha.com/symbol/HPE/news?source=feed_symbol_HPE</link>
<guid isPermaLink="false">https://seekingalpha.com/MarketCurrent:4649989</guid>
<pubDate>Fri, 02 Oct 2026 16:59:21 -0400</pubDate><sa:author_name>SA News</sa:author_name>
<sa:stock><sa:symbol>HPE</sa:symbol></sa:stock></item>
<item><title>HPE in spotlight as Citi ups price target</title>
<link>https://seekingalpha.com/symbol/HPE/news?source=feed_symbol_HPE</link>
<guid isPermaLink="false">https://seekingalpha.com/MarketCurrent:4648988</guid>
<pubDate>Thu, 01 Oct 2026 08:24:11 -0400</pubDate><sa:author_name>Chris Ciaccia</sa:author_name>
<sa:stock><sa:symbol>HPE</sa:symbol></sa:stock></item>
<item><title>Most and least shorted tech stocks</title>
<link>https://seekingalpha.com/symbol/HPE/news</link>
<guid isPermaLink="false">https://seekingalpha.com/MarketCurrent:1</guid>
<pubDate>Thu, 01 Oct 2026 08:00:00 -0400</pubDate>
<sa:stock><sa:symbol>HPE</sa:symbol></sa:stock><sa:stock><sa:symbol>A</sa:symbol></sa:stock>
<sa:stock><sa:symbol>B</sa:symbol></sa:stock><sa:stock><sa:symbol>C</sa:symbol></sa:stock>
<sa:stock><sa:symbol>D</sa:symbol></sa:stock><sa:stock><sa:symbol>E</sa:symbol></sa:stock>
<sa:stock><sa:symbol>F</sa:symbol></sa:stock></item>
<item><title>HPE: A Buy After Investor Day</title>
<link>https://seekingalpha.com/article/4951168-hpe-buy?source=feed_symbol_HPE</link>
<guid isPermaLink="false">https://seekingalpha.com/Article:4951168</guid>
<pubDate>Wed, 30 Sep 2026 17:32:56 -0400</pubDate><sa:author_name>Some Author</sa:author_name>
<sa:stock><sa:symbol>HPE</sa:symbol></sa:stock><sa:stock><sa:symbol>HPE.PR.C</sa:symbol></sa:stock></item>
<item><title>Other company story</title><link>https://seekingalpha.com/x</link>
<guid>https://seekingalpha.com/MarketCurrent:2</guid><pubDate>Wed, 30 Sep 2026 10:00:00 -0400</pubDate>
<sa:stock><sa:symbol>MSFT</sa:symbol></sa:stock></item>
</channel></rss>"""


def test_each_story_gets_its_own_url_and_roundups_are_skipped():
    rows = SeekingAlphaSource(engine=None).parse(FEED, "HPE", 25)
    assert [r.headline for r in rows] == [
        "HPE climbs 7% to new 52-week high", "HPE in spotlight as Citi ups price target", "HPE: A Buy After Investor Day"]
    assert [r.url for r in rows] == ["https://seekingalpha.com/news/4649989", "https://seekingalpha.com/news/4648988",
                                     "https://seekingalpha.com/article/4951168"]
    assert rows[0].published_at.isoformat() == "2026-10-02T20:59:21+00:00"
    assert rows[0].publisher == "Seeking Alpha" and rows[0].tags == () and rows[2].tags == ("Analysis",)
    assert len(SeekingAlphaSource(engine=None).parse(FEED, "HPE", 1)) == 1


def test_seeking_alpha_is_a_default_source():
    assert "seeking_alpha" in DEFAULT_SOURCES and SOURCE_CLASSES["seeking_alpha"] is SeekingAlphaSource
