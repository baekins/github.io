from polybet.parsing import extract_slug


def test_extract_market_slug_with_query_and_fragment():
    kind, slug = extract_slug("https://polymarket.com/market/lakers-celtics/?foo=1#x")
    assert kind == "market"
    assert slug == "lakers-celtics"


def test_extract_event_slug_trailing_slash():
    kind, slug = extract_slug("https://polymarket.com/event/nba-finals/")
    assert kind == "event"
    assert slug == "nba-finals"
