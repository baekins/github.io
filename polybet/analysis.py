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

try:
    from .odds_api import fetch_external_odds
except ImportError:
    fetch_external_odds = None

SEOUL = ZoneInfo("Asia/Seoul")


def _fmt_dt(dt):
    if dt is None:
        return "알 수 없음"
    return dt.astimezone(SEOUL).strftime("%Y-%m-%d %H:%M:%S KST")


def _bar(ratio, width=20):
    filled = int(ratio * width)
    return "█" * filled + "░" * (width - filled)


def _grade_market(liquidity, volume24hr, spread_avg):
    score = 0
    if liquidity and liquidity >= 50000:
        score += 3
    elif liquidity and liquidity >= 10000:
        score += 2
    elif liquidity and liquidity >= 1000:
        score += 1

    if volume24hr and volume24hr >= 10000:
        score += 2
    elif volume24hr and volume24hr >= 1000:
        score += 1

    if spread_avg is not None and spread_avg <= 0.02:
        score += 2
    elif spread_avg is not None and spread_avg <= 0.05:
        score += 1

    if score >= 6:
        return "A", "우수"
    elif score >= 4:
        return "B", "양호"
    elif score >= 2:
        return "C", "보통"
    else:
        return "D", "주의"


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
            out.best_bid, out.best_ask, out.mid, out.spread = None, None, None, None

        out.fee_rate_bps = await clob.fetch_fee_rate(out.token_id)
        if out.fee_rate_bps is None:
            fee_unknown = True

    return snapshot, fee_unknown


async def analyze(text: str, ref_odds_text: str = "") -> str:
    geo_msg = geoblock_status_message()

    slug_type, slug = extract_slug(text)
    gamma = GammaClient()

    if slug_type and slug:
        snapshot = await gamma.fetch_market(slug)
        if not snapshot:
            candidates = await collect_search_candidates(gamma, slug)
            best = choose_best_candidate(candidates)
            if not best:
                return f"오류: '{slug}' 마켓을 찾을 수 없습니다."
            snapshot = await gamma.fetch_market(best.slug)
    else:
        candidates = await collect_search_candidates(gamma, text)
        best = choose_best_candidate(candidates)
        if not best:
            return f"오류: '{text}'에 대한 마켓을 찾을 수 없습니다."
        snapshot = await gamma.fetch_market(best.slug)

    if not snapshot:
        return "오류: 마켓 데이터를 가져올 수 없습니다."

    snapshot, fee_unknown = await _hydrate_clob(snapshot)

    # 외부 배당률 수집 시도
    external_odds = {}
    if fetch_external_odds is not None:
        try:
            external_odds = await fetch_external_odds(snapshot.title)
        except Exception:
            external_odds = {}

    # 참조 배당률 (사용자 입력 or 외부 API)
    ref_odds = parse_reference_odds(ref_odds_text) if ref_odds_text else {}

    prices = {o.name: o.price for o in snapshot.outcomes}
    mids = {}
    if SETTINGS.enable_clob_reads:
        mids = {o.name: o.mid for o in snapshot.outcomes if o.mid is not None}
    fair_probs = blended_fair_probs(prices, mids, ref_odds)

    # 결과 구성
    lines = []
    lines.append(f"# {snapshot.title}")
    lines.append(f"Title: {snapshot.title}")

    # ── 1) 마켓 정보 ──
    lines.append("")
    lines.append("## 1) 📊 마켓 정보")
    lines.append(f"  상태: {'🟢 활성' if snapshot.active else '🔴 비활성'} | {'마감됨' if snapshot.closed else '진행중'}")
    lines.append(f"  시작: {_fmt_dt(snapshot.start_date)}")
    lines.append(f"  조회: {_fmt_dt(snapshot.fetched_at)}")

    if geo_msg:
        lines.append(f"  ⚠️ {geo_msg}")

    # ── 2) 배당률 ──
    lines.append("")
    lines.append("## 2) 📈 배당률 분석")
    for outcome in sorted(snapshot.outcomes, key=lambda o: o.price, reverse=True):
        pct = outcome.price * 100
        bar = _bar(outcome.price)
        lines.append(f"  {outcome.name}")
        lines.append(f"    Polymarket: {pct:5.1f}% {bar}")
        if SETTINGS.enable_clob_reads and outcome.mid is not None:
            mid_pct = outcome.mid * 100
            lines.append(f"    Mid가격:    {mid_pct:5.1f}% | 스프레드: {outcome.spread:.4f}" if outcome.spread else f"    Mid가격:    {mid_pct:5.1f}%")
        fair = fair_probs.get(outcome.name, outcome.price)
        fair_pct = fair * 100
        lines.append(f"    공정확률:   {fair_pct:5.1f}% {_bar(fair)}")

    # ── 3) 외부 배당률 비교 ──
    if external_odds:
        lines.append("")
        lines.append("## 3) 🌐 외부 북메이커 배당률")
        for bookie, odds_data in external_odds.items():
            lines.append(f"  [{bookie}]")
            for name, odd in odds_data.items():
                impl_prob = (1.0 / odd) * 100 if odd > 0 else 0
                lines.append(f"    {name}: {odd:.2f} (내재확젔 {impl_prob:.1f}%)")
    else:
        lines.append("")
        lines.append("## 3) 🌐 외부 배당률")
        lines.append("  외부 배당률 데이터 없음")
        lines.append("  (The Odds API 키를 .env에 설정하면 자동 수집)")

    # ── 4) 마켓 품질 ──
    spread_vals = [o.spread for o in snapshot.outcomes if o.spread is not None]
    spread_avg = sum(spread_vals) / len(spread_vals) if spread_vals else None
    grade, grade_text = _grade_market(snapshot.liquidity, snapshot.volume24hr, spread_avg)

    lines.append("")
    lines.append("## 4) 🏦 마켓 품질")
    lines.append(f"  등급: {grade} ({grade_text})")

    liq = snapshot.liquidity
    vol = snapshot.volume24hr
    oi = snapshot.open_interest

    liq_str = f"${liq:,.0f}" if liq is not None else "알 수 없음"
    vol_str = f"${vol:,.0f}" if vol is not None else "알 수 없음"
    oi_str = f"${oi:,.0f}" if oi is not None else "알 수 없음"

    lines.append(f"  유동성:    {liq_str}")
    lines.append(f"  24h거래량: {vol_str}")
    lines.append(f"  미결제약정: {oi_str}")
    if spread_avg is not None:
        lines.append(f"  평균스프레드: {spread_avg:.4f}")
    if fee_unknown:
        lines.append("  ⚠️ 수수료 정보 일부 누락")

    # ── 5) 투자 판단 ──
    lines.append("")
    lines.append("## 5) 💰 투자 판단")

    recommended = []
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

        edge_pct = edge * 100
        ev_pct = ev * 100
        emoji = "✅" if decision == "RECOMMEND" else "❌"

        lines.append(f"  {emoji} {outcome.name}")
        lines.append(f"    엣지: {edge_pct:+.2f}% | EV: {ev_pct:+.2f}%")
        lines.append(f"    비용: {cost.total:.4f} (스프레드={cost.spread:.4f}, 수수료={cost.fee:.4f}, 슬리피지={cost.slippage:.4f})")

        if decision == "RECOMMEND":
            kelly = fractional_kelly_fraction(fair, outcome.price, SETTINGS.kelly_fraction)
            lines.append(f"    켈리비율: {kelly:.2%} | decision=RECOMMEND")
            recommended.append((outcome.name, fair, outcome.price))
        else:
            reasons = []
            if not ev_ok:
                reasons.append(f"EV {ev_pct:.2f}% < 기준 {SETTINGS.ev_min*100:.1f}%")
            if not liq_ok:
                reasons.append(f"유동성 ${liq or 0:,.0f} < 기준 ${SETTINGS.liq_min:,.0f}")
            if not spread_ok:
                reasons.append(f"스프레드 {spread_for_gate:.4f} > 기준 {SETTINGS.spread_max:.4f}")
            lines.append(f"    사유: {', '.join(reasons) if reasons else '조건 미달'} | decision=PASS")

    # ── 6) 최종 요약 ──
    lines.append("")
    lines.append("## 6) 📋 최종 요약")
    if recommended:
        for name, fair, price in recommended:
            confidence = "높음" if abs(fair - price) > 0.05 else "보통"
            lines.append(f"  ✅ {name} 매수 추천")
            lines.append(f"     Confidence: {confidence}")
            lines.append(f"     현재가 {price:.4f} → 공정가 {fair:.4f}")
    else:
        lines.append("  현재 추천 종목 없음")
        lines.append("  모든 결과가 EV/유동성/스프레드 기준 미달")

    lines.append("")
    lines.append(f"Polybet v1.1 | 분석 완료")

    return "\n".join(lines)
