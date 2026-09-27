"""Tests for the bounded RSS backfill. Appended to tests/test_momx_news.py."""
import io

TESTS = '''

# ---------------------------------------------------------------------------
# RSS backfill: cover the symbols Benzinga does not, without becoming the next
# rate incident. Benzinga answered 123 of 358 rows on 2026-09-04; NBIS's newest
# Benzinga headline was ~53h old while Yahoo had one 6.1h old.
# ---------------------------------------------------------------------------
def _rss_body(items):
    """Minimal RSS 2.0, RFC-822 dates - the shape Yahoo actually returns."""
    parts = ["<?xml version='1.0'?><rss version='2.0'><channel>"]
    for title, pub in items:
        parts.append(
            "<item><title>%s</title><pubDate>%s</pubDate>"
            "<link>https://example.test/a</link></item>" % (title, pub)
        )
    parts.append("</channel></rss>")
    return "".join(parts)


def _rfc822(minutes_ago):
    import email.utils
    from datetime import datetime, timedelta, timezone
    return email.utils.format_datetime(
        datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    )


class _RssResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code


def _rss_get(bodies, calls):
    """Injected getter: records every URL so the budget can be asserted."""
    def get(url, **kwargs):
        calls.append(url)
        for symbol, body in bodies.items():
            if "s=%s&" % symbol in url or url.endswith("s=%s" % symbol):
                return _RssResponse(body)
        return _RssResponse("", status_code=404)
    return get


def test_rss_backfills_a_symbol_benzinga_missed():
    from datetime import datetime, timedelta, timezone
    from momx import news
    news.reset_caches()
    calls = []
    bodies = {"NBIS": _rss_body([("Nebius wins AI infrastructure deal", _rfc822(366))])}
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    out = news._rss_backfill(["NBIS"], cutoff, _rss_get(bodies, calls))
    assert out["NBIS"]["headline"] == "Nebius wins AI infrastructure deal"
    assert out["NBIS"]["source"] == "Yahoo Finance"
    assert out["NBIS"]["scope"] == "specific"
    assert len(calls) == 1


def test_rss_never_exceeds_its_per_build_budget():
    """The whole safety argument: RSS is one request per ticker, and this app
    has been IP-banned twice this week for request volume."""
    from datetime import datetime, timedelta, timezone
    from momx import news
    news.reset_caches()
    calls = []
    missing = ["SYM%03d" % i for i in range(200)]
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    news._rss_backfill(missing, cutoff, _rss_get({}, calls))
    assert len(calls) == news.RSS_BACKFILL_PER_BUILD, len(calls)


def test_a_miss_is_cached_so_it_does_not_burn_a_slot_every_build():
    from datetime import datetime, timedelta, timezone
    from momx import news
    news.reset_caches()
    calls = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    get = _rss_get({}, calls)
    news._rss_backfill(["AAAA"], cutoff, get)
    news._rss_backfill(["AAAA"], cutoff, get)
    assert len(calls) == 1, "a symbol with no news must not be refetched next build"


def test_an_rss_listicle_is_demoted_not_dropped():
    from datetime import datetime, timedelta, timezone
    from momx import news
    news.reset_caches()
    calls = []
    bodies = {"XYZ": _rss_body([
        ("3 Founder Backed Growth Stocks To Watch In September 2026", _rfc822(30)),
    ])}
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    out = news._rss_backfill(["XYZ"], cutoff, _rss_get(bodies, calls))
    assert out["XYZ"]["scope"] == "market-wide"
    assert out["XYZ"]["headline"].startswith("3 Founder Backed")


def test_rss_items_older_than_the_window_are_ignored():
    from datetime import datetime, timedelta, timezone
    from momx import news
    news.reset_caches()
    calls = []
    bodies = {"OLD": _rss_body([("Ancient story", _rfc822(60 * 48))])}
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    out = news._rss_backfill(["OLD"], cutoff, _rss_get(bodies, calls))
    assert out == {}


def test_rss_picks_the_newest_item_not_the_first():
    from datetime import datetime, timedelta, timezone
    from momx import news
    news.reset_caches()
    calls = []
    bodies = {"NEW": _rss_body([
        ("Older story", _rfc822(600)),
        ("Newest story", _rfc822(20)),
        ("Middle story", _rfc822(300)),
    ])}
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    out = news._rss_backfill(["NEW"], cutoff, _rss_get(bodies, calls))
    assert out["NEW"]["headline"] == "Newest story"


def test_a_broken_feed_can_never_break_the_board():
    from datetime import datetime, timedelta, timezone
    from momx import news
    news.reset_caches()

    def exploding_get(url, **kwargs):
        raise RuntimeError("feed down")

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    assert news._rss_backfill(["ANY"], cutoff, exploding_get) == {}


def test_rss_can_be_switched_off():
    import os
    from datetime import datetime, timedelta, timezone
    from momx import news
    news.reset_caches()
    calls = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    os.environ[news.RSS_ENV] = "0"
    try:
        out = news._rss_backfill(["NBIS"], cutoff, _rss_get({}, calls))
    finally:
        os.environ.pop(news.RSS_ENV, None)
    assert out == {}
    assert calls == []
'''

p = "tests/test_momx_news.py"
s = io.open(p, encoding="utf-8", newline="").read()
assert "_rss_backfill" not in s, "already appended"
io.open(p, "w", encoding="utf-8", newline="").write(s.rstrip("\n") + "\n" + TESTS)
print("appended RSS backfill tests")
