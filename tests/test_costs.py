from polybet.costs import estimate_cost_for_outcome
from polybet.models import Outcome


def test_cost_model_with_spread_and_fee():
    outcome = Outcome(name="Yes", price=0.54, spread=0.02, fee_rate_bps=30)
    cost = estimate_cost_for_outcome(outcome, liquidity=15000)
    assert round(cost.spread, 4) == 0.01
    assert round(cost.fee, 4) == 0.003
    assert round(cost.total, 4) > 0.01
