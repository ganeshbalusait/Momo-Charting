"""Jev news gates (2026-10-10): the headline must be about the ticker, and a
▲ / ▼ needs Jev 70%+ sure. Cases are from the blind check of Oct 5-9."""
from momx import news_relevance as nr

HD = "Home Depot, Inc. (The) Common Stock"


def test_about_ticker_cases_from_the_blind_check():
    assert nr.about_ticker("HD", "ERock, HD Construction Equipment Sign Multi-Year Engine Supply Agreement", HD) is False
    assert nr.about_ticker("HD", "Home Depot raises outlook", HD) is True
    assert nr.about_ticker("HD", "Analysts like $HD into earnings", HD) is True
    assert nr.about_ticker("NVTS", "IonQ shares are trading higher after DARPA contract",
                           "Navitas Semiconductor Corporation - Common Stock") is False
    assert nr.about_ticker("AMZN", "Amazon AI Boom: Analysts See AWS Driving Growth", "Amazon.com, Inc. - Common Stock") is True
    assert nr.about_ticker("BA", "Why Boeing Stock Isn't Up More After Good FAA News", "Boeing Company (The) Common Stock") is True
    assert nr.about_ticker("GS", "Goldman Sachs expected to lead trading revenue", "Goldman Sachs Group, Inc. (The) Common Stock") is True
    assert nr.about_ticker("UPS", "UPS error routed F-35 parts through Hong Kong", "United Parcel Service, Inc. Common Stock") is True
    assert nr.about_ticker("SPY", "Stocks slide as crude soars", "SPDR S&P 500 ETF Trust", is_etf=True) is None
    assert nr.about_ticker("XYZQ", "Something happened", None) is None      # no name on file: never a guess


def test_effective_keeps_confident_on_topic_reads_only():
    read = {"label": "bullish", "p": 0.87}
    off = nr.effective(read, "HD", "ERock, HD Construction Equipment deal", HD)
    assert off["label"] == "not_important" and off["raw"] == "bullish" and off["why"] == "not about HD"
    weak = nr.effective({"label": "bearish", "p": 0.85}, "HD", "Home Depot recall", HD)
    assert weak["label"] == "not_important" and "85%" in weak["why"]
    assert nr.effective({"label": "bullish", "p": 0.9}, "HD", "Home Depot beats", HD)["label"] == "bullish"
    assert nr.effective({"label": "not_important", "p": 0.9}, "HD", "x", HD)["label"] == "not_important"


def test_routine_items_get_no_arrow_but_real_moves_do():
    assert nr.routine("AppLovin Corporation Sued for Securities Law Violations - Contact the DJS Law Group") == "law-firm advert"
    assert nr.routine("Coca-Cola: Reiterating 'Sell' Amid Insider Selling") == "rating repeated"
    assert nr.routine("What's Going On With Snap Stock Friday?") == "question headline"
    assert nr.routine("Synopsys Stock Fair Value Edges Higher After AI Deals") == "opinion / valuation piece"
    assert nr.routine("Why Is AST SpaceMobile Stock Surging on Tuesday?") is None       # a real move
    assert nr.routine("FuelCell Energy Stock Gains 14% Today - Here's Why") is None
    assert nr.routine("Marvell jumps after boosting guidance at investor day") is None
    assert nr.effective({"label": "bearish", "p": 0.99}, "APP", "AppLovin Sued - Contact the DJS Law Group",
                        "AppLovin Corporation - Class A Common Stock")["label"] == "not_important"
    assert nr.MIN_P == 0.90
