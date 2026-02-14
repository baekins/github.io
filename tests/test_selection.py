from datetime import datetime, timedelta, timezone

from polybet.clients import choose_best_candidate, collect_search_candidates
from polybet.models import Candidate


def test_choose_best_candidate_by_liq_then_volume_then_time():
    now = datetime.now(timezone.utc)
    cands = [
        Candidate(title="A", slug="a", active=True, closed=False, liquidity=1000, volume24hr=500, start_date=now + timedelta(hours=5)),
        Candidate(title="B", slug="b", active=True, closed=False, liquidity=2000, volume24hr=100, start_date=now + timedelta(hours=10)),
        Candidate(title="C", slug="c", active=True, closed=False, liquidity=2000, volume24hr=300, start_date=now + timedelta(hours=20)),
    ]
    best = choose_best_candidate(cands)
    assert best is not None
    assert best.slug == "c"


def test_collect_search_candidates_marks_sports_related():
    payload = {
        "markets": [
            {"slug": "nba-a", "question": "Lakers vs Celtics", "active": True, "closed": False, "liquidity": 1000, "tags": ["sports", "nba"]},
            {"slug": "crypto-b", "question": "Will BTC hit 200k?", "active": True, "closed": False, "liquidity": 5000, "tags": ["crypto"]},
        ]
    }
    out = collect_search_candidates(payload)
    assert len(out) == 2
    assert out[0].sports_related is True
    assert out[1].sports_related is False
