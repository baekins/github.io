from __future__ import annotations

import asyncio
from datetime import timezone
from zoneinfo import ZoneInfo

from .clients import ClobClient, GammaClient, choose_best_candidate, collect_search_candidates, parse_market_payload
from .config import SETTINGS
from .costs import estimate_cost_for_outcome
from .geoblock import geoblock_status_message
from .math_utils import blended_fair_probs, devig_decimal_odds, fractional_kelly_fraction
from .models import Candidate, MarketSnapshot
from .parsing import extract_slug, parse_reference_odds

SEOUL = ZoneInfo("Asia/Seoul")


def _fmt_dt(dt):
    if dt is None:
        return "unknown"
    return dt.astimezone(SEOUL).strftime("%Y-%m-%d %H:%M:%S %Z")


async def _hydrate_clob(snapshot: MarketSnapshot) -> tuple[MarketSnapshot, bool]:
    if not SETTINGS.enable_clob_reads:
        return snapshot, False

    clob = ClobClient()
    fee_unknown = False
    for out in snapshot.outcomes:
        if not out.token_id:
            fee_unknown = True
            continue
        try:
            book = await clob.fetch_book(out.token_id)
            bids = book.get("bids") or []
            asks = book.get("asks") or []
            out.best_bid = float(bids[0].get("price")) if bids else None
            out.best_ask = float(asks[0].get("price")) if asks else None
            if out.best_bid is not None and out.best_ask is not None:
                out.mid = (out.best_bid + out.best_ask) / 2.0
                out.spread = max(0.0, out.best_ask - out.best_bid)
        except Exception:
            # Deterministic fail-open: unknown microstructure fields.
            out.best_bid, out.best_ask, out.mid, out.spread = None, None, None, None

        out.fee_rate_bps = await clob.fetch_fee_rate(out.token_id)
        if out.fee_rate_bps is None:
            fee_unknown = True

    return snapshot, fee_unknown


async def _resolve_market(input_text: str) -> tuple[MarketSnapshot, list[Candidate]]:
    gamma = GammaClient()
    kind, slug = extract_slug(input_text)

    if kind == "event" and slug:
        event = await gamma.fetch_event_by_slug(slug)
        markets = event.get("markets") or []
        if markets:
            return parse_market_payload(markets[0]), []
        raise ValueError("Event has no market payload")

    if kind == "market" and slug:
        mkt = await gamma.fetch_market_by_slug(slug)
        return parse_market_payload(mkt), []

    search_data = await gamma.search(input_text)
    candidates = collect_search_candidates(search_data)
    best = choose_best_candidate(candidates)
    if not best:
        raise ValueError("No active candidate found from search")

    if best.type == "event":
        event = await gamma.fetch_event_by_slug(best.slug)
        markets = event.get("markets") or []
        if not markets:
            raise ValueError("Selected event has no active markets")
        return parse_market_payload(markets[0]), candidates[:5]

    mkt = await gamma.fetch_market_by_slug(best.slug)
    return parse_market_payload(mkt), candidates[:5]


async def analyze_async(input_text: str) -> str:
    snapshot, candidates = await _resolve_market(input_text)
    snapshot, fee_unknown = await _hydrate_clob(snapshot)

    market_probs = {o.name: o.price for o in snapshot.outcomes}
    odds_map = parse_reference_odds(input_text)
    ref_probs = devig_decimal_odds(odds_map) if odds_map else None
    fair_probs, fair_label = blended_fair_probs(market_probs, ref_probs)

    confidence = "medium"
    if not ref_probs:
        confidence = "low"
    if fee_unknown and SETTINGS.enable_clob_reads:
        confidence = "low"

    lines: list[str] = ["# Polymarket Sports Auto-Analyst", "", "## 1) Market Snapshot"]
    lines.append(f"- Title: {snapshot.title}")
    lines.append(f"- Slug: `{snapshot.slug}`")
    lines.append(f"- Start (Asia/Seoul): {_fmt_dt(snapshot.start_date)}")
    lines.append(f"- End (Asia/Seoul): {_fmt_dt(snapshot.end_date)}")
    lines.append(f"- Fetch timestamp (UTC): {snapshot.fetched_at.astimezone(timezone.utc).isoformat()}")
    lines.append("- Outcomes:")
    for outcome in sorted(snapshot.outcomes, key=lambda o: o.name.lower()):
        lines.append(f"  - {outcome.name}: price={outcome.price:.4f}")
        if SETTINGS.enable_clob_reads:
            lines.append(
                f"    - bestBid={outcome.best_bid if outcome.best_bid is not None else 'unknown'}, "
                f"bestAsk={outcome.best_ask if outcome.best_ask is not None else 'unknown'}, "
                f"mid={outcome.mid if outcome.mid is not None else 'unknown'}, "
                f"spread={outcome.spread if outcome.spread is not None else 'unknown'}"
            )

    lines.extend(["", "## 2) Market Quality + Costs"])
    lines.append(f"- liquidity: {snapshot.liquidity if snapshot.liquidity is not None else 'unknown'}")
    lines.append(f"- volume24hr: {snapshot.volume24hr if snapshot.volume24hr is not None else 'unknown'}")
    lines.append(f"- openInterest: {snapshot.open_interest if snapshot.open_interest is not None else 'unknown'}")

    lines.extend(["", "## 3) Fair Probability Engine"])
    lines.append(f"- Mode: {fair_label}")
    if odds_map:
        lines.append("- Parsed reference odds:")
        for name, odd in sorted(odds_map.items(), key=lambda item: item[0].lower()):
            lines.append(f"  - {name}: decimal_odds={odd:.3f}")
        lines.append("- Reference confidence band: medium (manual odds provided)")
    else:
        lines.append("- Reference odds: unknown")
        lines.append("- Reference confidence band: low")

    if SETTINGS.enable_clob_reads and fee_unknown:
        lines.append("- Fee data: unknown for at least one token_id (confidence reduced)")
    lines.append(f"- Confidence: {confidence}")

    lines.extend(["", "## 4) Edge + EV (after estimated costs)"])
    recommended: list[tuple[str, float, float]] = []
    pass_reasons: list[str] = []
    for outcome in sorted(snapshot.outcomes, key=lambda o: o.name.lower()):
        fair = fair_probs.get(outcome.name, outcome.price)
        edge = fair - outcome.price
        cost = estimate_cost_for_outcome(outcome, snapshot.liquidity)
        ev = edge - cost.total
        liq_ok = (snapshot.liquidity or 0.0) >= SETTINGS.liq_min
        spread_for_gate = outcome.spread if outcome.spread is not None else 0.01
        spread_ok = spread_for_gate <= SETTINGS.spread_max
        ev_ok = ev >= SETTINGS.ev_min

        decision = "RECOMMEND" if liq_ok and spread_ok and ev_ok else "PASS"
        lines.append(
            f"- {outcome.name}: fair={fair:.4f}, edge={edge:.4f}, cost={cost.total:.4f} "
            f"(spread={cost.spread:.4f}, fee={cost.fee:.4f}, slippage={cost.slippage:.4f}), "
            f"EV≈{ev:.4f}, decision={decision}"
        )

        if decision == "RECOMMEND":
            recommended.append((outcome.name, fair, outcome.price))
        else:
            reasons = []
            if not ev_ok:
                reasons.append("EV below threshold")
            if not liq_ok:
                reasons.append("liquidity below LIQ_MIN")
            if not spread_ok:
                reasons.append("spread above SPREAD_MAX")
            pass_reasons.append(f"{outcome.name}: {', '.join(reasons)}")

    if not recommended:
        lines.append("- Overall: PASS")
        lines.append("- PASS reasons:")
        for reason in pass_reasons:
            lines.append(f"  - {reason}")

    lines.extend(["", "## 5) Position Sizing (risk-first)"])
    bankroll = SETTINGS.default_bankroll
    lines.append(f"- Bankroll assumption: {bankroll:.2f} (default because bankroll was not provided)")
    daily_cap = SETTINGS.max_daily_exposure * bankroll

    if not recommended:
        lines.append("- No positions sized because recommendation is PASS.")
    else:
        for name, fair, price in recommended:
            raw_fraction = fractional_kelly_fraction(fair, price, SETTINGS.fractional_kelly)
            capped_fraction = min(raw_fraction, SETTINGS.max_bet_pct)
            amount = capped_fraction * bankroll
            lines.append(
                f"- {name}: fracKelly={raw_fraction:.4f}, capped={capped_fraction:.4f}, "
                f"suggested_bet=${amount:.2f}, daily_exposure_cap=${daily_cap:.2f}"
            )

    lines.extend(["", "## 6) What Would Change My Mind"])
    lines.append("1. Verified injury/lineup change from an official source.")
    lines.append("2. Weather/venue update relevant to expected game state.")
    lines.append("3. External odds move >2% after de-vig normalization.")
    lines.append("4. Liquidity jump that lowers estimated slippage/spread.")
    lines.append("5. Official start time update affecting player availability.")

    lines.extend(["", "## Compliance", f"- {geoblock_status_message()}"])

    if candidates:
        lines.extend(["", "## Other top candidates (up to 5)"])
        for candidate in candidates[:5]:
            lines.append(
                f"- {candidate.title} (`{candidate.slug}`) active={candidate.active} closed={candidate.closed} "
                f"liquidity={candidate.liquidity} volume24hr={candidate.volume24hr} sports_related={candidate.sports_related}"
            )

    return "\n".join(lines)


def analyze(input_text: str) -> str:
    return asyncio.run(analyze_async(input_text))
