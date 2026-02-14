from __future__ import annotations


def normalize_probs(values: dict[str, float]) -> dict[str, float]:
    total = sum(v for v in values.values() if v >= 0)
    if total <= 0:
        return {k: 0.0 for k in values}
    return {k: max(0.0, v) / total for k, v in values.items()}


def devig_decimal_odds(odds: dict[str, float]) -> dict[str, float]:
    inv = {k: 1.0 / v for k, v in odds.items() if v > 1.0}
    return normalize_probs(inv)


def blended_fair_probs(prices: dict[str, float], mids: dict[str, float] | None = None, ref: dict[str, float] | None = None) -> tuple[dict[str, float], str]:
    """Blend market prices, CLOB mid-prices, and external reference odds."""
    sources = [("market", prices)]
    if mids:
        sources.append(("mid", mids))
    if ref:
        sources.append(("ref", ref))

    if len(sources) == 1:
        return normalize_probs(dict(prices)), "NO EDGE (no external reference)"

    all_outcomes = set(prices.keys())
    for _, s in sources:
        all_outcomes |= set(s.keys())

    out: dict[str, float] = {}
    if ref and mids:
        # 3-source blend: 50% ref, 30% mid, 20% market
        for o in all_outcomes:
            p = prices.get(o, 0.0)
            m = mids.get(o, p)
            r = ref.get(o, p)
            out[o] = 0.5 * r + 0.3 * m + 0.2 * p
        return normalize_probs(out), "Blended 50% ref / 30% mid / 20% market"
    elif ref:
        # 2-source: 70% ref, 30% market
        for o in all_outcomes:
            p = prices.get(o, 0.0)
            r = ref.get(o, p)
            out[o] = 0.7 * r + 0.3 * p
        return normalize_probs(out), "Blended 70% ref / 30% market"
    else:
        # 2-source: 60% mid, 40% market
        for o in all_outcomes:
            p = prices.get(o, 0.0)
            m = mids.get(o, p)
            out[o] = 0.6 * m + 0.4 * p
        return normalize_probs(out), "Blended 60% mid / 40% market"


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
