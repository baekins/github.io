from polybet.math_utils import devig_decimal_odds, fractional_kelly_fraction


def test_devig_two_way():
    probs = devig_decimal_odds({"A": 1.9, "B": 1.9})
    assert round(probs["A"], 4) == 0.5
    assert round(probs["B"], 4) == 0.5


def test_devig_three_way():
    probs = devig_decimal_odds({"Home": 2.2, "Draw": 3.4, "Away": 3.0})
    assert round(sum(probs.values()), 6) == 1.0


def test_fractional_kelly_non_negative():
    f = fractional_kelly_fraction(prob=0.55, price=0.5, fraction=0.25)
    assert f > 0
    assert f < 0.25
