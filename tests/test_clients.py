from polybet.clients import parse_market_payload


def test_parse_market_payload_handles_json_string_fields():
    payload = {
        "slug": "abc",
        "question": "Team A vs Team B",
        "active": True,
        "closed": False,
        "outcomes": '["Team A", "Team B"]',
        "outcomePrices": '["0.42", "0.58"]',
        "clobTokenIds": ["1", "2"],
    }
    snap = parse_market_payload(payload)
    assert len(snap.outcomes) == 2
    assert snap.outcomes[0].name == "Team A"
    assert snap.outcomes[0].price == 0.42
    assert snap.outcomes[1].token_id == "2"
