from __future__ import annotations


def normalize_probs(values: dict[str, float]) -> dict[str, float]:
    total = sum(v for v in values.values() if v >= 0)
    if total <= 0:
        return {k: 0.0 for k in values}
    return {k: max(0.0, v) / total for k, v in values.items()}


def devig_decimal_odds(odds: dict[str, float]) -> dict[str, float]:
    inv = {k: 1.0 / v for k, v in odds.items() if v > 1.0}
    return normalize_probs(inv)


def blended_fair_probs(mkt: dict[str, float], ref: dict[str, float] | None) -> tuple[dict[str, float], str]:
    if not ref:
        return normalize_probs(dict(mkt)), "NO EDGE (no external reference)"

    out: dict[str, float] = {}
    for outcome, mkt_prob in mkt.items():
        out[outcome] = 0.7 * ref.get(outcome, mkt_prob) + 0.3 * mkt_prob
    return normalize_probs(out), "Blended 70% reference / 30% market"


def fractional_kelly_fraction(prob: float, price: float, fraction: float) -> float:
    if not (0 < price < 1) or not (0 <= prob <= 1):
        return 0.0
    b = (1 - price) / price
    q = 1 - prob
    kelly = (b * prob - q) / b
    return max(0.0, kelly * max(0.0, fraction))


def slippage_heuristic(liquidity: float | None) -> float:
    if liquidity is None:
        return 0.02
    if liquidity < 2_000:
        return 0.03
    if liquidity < 10_000:
        return 0.015
    return 0.0075
