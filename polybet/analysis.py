"""Polybet – 스포츠 베팅 분석 엔진 (이벤트 레벨 다중 마켓 지원)"""
from __future__ import annotations

import asyncio
from datetime import timezone
from zoneinfo import ZoneInfo

from .clients import ClobClient, GammaClient, parse_market_payload
from .config import SETTINGS
from .costs import estimate_cost_for_outcome
from .geoblock import geoblock_status_message
from .math_utils import devig_decimal_odds, fractional_kelly_fraction
from .models import Candidate, MarketSnapshot
from .parsing import extract_slug, parse_reference_odds

try:
    from .odds_api import fetch_external_odds
except ImportError:
    fetch_external_odds = None

SEOUL = ZoneInfo("Asia/Seoul")


def _fmt_dt(dt):
    if dt is None:
        return "정보 없음"
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
        return "A", "매우 좋음"
    elif score >= 4:
        return "B", "양호"
    elif score >= 2:
        return "C", "보통"
    else:
        return "D", "낮음"


async def _fetch_event_markets(gamma: GammaClient, slug: str):
    """이벤트 slug로 모든 마켓을 가져와서 리스트로 반환"""
    try:
        raw = await gamma.fetch_event_by_slug(slug)
        if raw and isinstance(raw, dict):
            markets_raw = raw.get("markets", [])
            if markets_raw:
                event_title = raw.get("title", "Unknown Event")
                markets = []
                for m in markets_raw:
                    snap = parse_market_payload(m)
                    markets.append(snap)
                return event_title, markets
    except Exception:
        pass
    return None, []


async def _fetch_single_market(gamma: GammaClient, slug: str):
    """단일 마켓 slug로 가져오기"""
    try:
        raw = await gamma.fetch_market_by_slug(slug)
        if raw:
            return parse_market_payload(raw)
    except Exception:
        pass
    return None


async def _hydrate_clob(snapshot: MarketSnapshot):
    """CLOB 데이터로 mid-price 보강"""
    if not SETTINGS.enable_clob_reads:
        return snapshot, True
    try:
        clob = ClobClient()
        for outcome in snapshot.outcomes:
            if outcome.token_id:
                book = await clob.get_order_book(outcome.token_id)
                if book:
                    best_bid = book.get("bids", [{}])[0] if book.get("bids") else {}
                    best_ask = book.get("asks", [{}])[0] if book.get("asks") else {}
                    bid_p = float(best_bid.get("price", 0))
                    ask_p = float(best_ask.get("price", 0))
                    if bid_p > 0 and ask_p > 0:
                        outcome.mid = (bid_p + ask_p) / 2
                        outcome.spread = ask_p - bid_p
        return snapshot, False
    except Exception:
        return snapshot, True


async def analyze(text: str, ref_odds_text: str = "") -> str:
    """메인 분석 함수 - 이벤트 레벨 + 다중 마켓 지원"""
    geo_msg = geoblock_status_message()
    slug_type, slug = extract_slug(text)
    gamma = GammaClient()

    event_title = None
    markets = []

    if slug_type and slug:
        # 1) 이벤트 레벨로 먼저 시도 (스포츠는 대부분 이벤트)
        event_title, markets = await _fetch_event_markets(gamma, slug)

        # 2) 이벤트가 아니메 단일 마켓으로
        if not markets:
            snap = await _fetch_single_market(gamma, slug)
            if snap:
                event_title = snap.title
                markets = [snap]

        # 3) 검색 폴백
        if not markets:
            search_result = await gamma.search(slug)
            items = search_result.get("markets", []) + search_result.get("events", [])
            if items:
                first = items[0]
                if first.get("markets"):
                    # 이벤트 결과
                    event_title = first.get("title", slug)
                    for m in first["markets"]:
                        markets.append(parse_market_payload(m))
                else:
                    snap = parse_market_payload(first)
                    event_title = snap.title
                    markets = [snap]
    else:
        # 텍스트 검색
        search_result = await gamma.search(text)
        items = search_result.get("markets", []) + search_result.get("events", [])
        if items:
            first = items[0]
            if first.get("markets"):
                event_title = first.get("title", text)
                for m in first["markets"]:
                    markets.append(parse_market_payload(m))
            else:
                snap = parse_market_payload(first)
                event_title = snap.title
                markets = [snap]

    if not markets:
        return f"오류: '{text}'에 대한 마켓을 찾을 수 없습니다."

    # CLOB 데이터 보강
    for i, snap in enumerate(markets):
        markets[i], _ = await _hydrate_clob(snap)

    # 외부 배당률 (The Odds API)
    ref_odds = parse_reference_odds(ref_odds_text) if ref_odds_text else {}
    ext_odds = {}
    if fetch_external_odds is not None and not ref_odds:
        try:
            ext_result = await fetch_external_odds(event_title or text)
            if ext_result:
                ext_odds = ext_result
        except Exception:
            pass

    # ── 분석 결과 구성 ──
    lines = []
    lines.append(f"# {event_title or markets[0].title}")
    lines.append("")

    # 이벤트인지 단일 마켓인지 판별
    is_event = len(markets) > 1

    # ══ 1) 마켓 정보 ══
    lines.append("## 1) 📊 마켟 정보")
    if is_event:
        lines.append(f"  이벤트 내 마켟 수: {len(markets)}개")
    for snap in markets:
        lines.append(f"  상태: {'🟢 활성' if snap.active else '🔴 비활성'} | {'마감됨' if snap.closed else '진행중'}")
        lines.append(f"  시작: {_fmt_dt(snap.start_date)}")
        break  # 첫 마켟 정보만
    lines.append(f"  조회: {_fmt_dt(markets[0].fetched_at)}")
    if geo_msg:
        lines.append(f"  ⚠️ {geo_msg}")

    # ══ 2) 배당률 분석 (이벤트 레벨) ══
    lines.append("")
    lines.append("## 2) 📈 배당률 분석")

    # 이벤트 내 모든 결과를 하나의 테이블로
    all_outcomes = []
    total_implied_prob = 0.0

    for snap in markets:
        # groupItemTitle이나 question에서 팀/결과명 추출
        group_name = snap.title.replace("Will ", "").replace(" win on ", " ").split("?")[0]
        yes_price = None
        for o in snap.outcomes:
            if o.name.lower() == "yes":
                yes_price = o.price
                mid = o.mid if o.mid else o.price
                spread = o.spread if hasattr(o, 'spread') and o.spread else None
                all_outcomes.append({
                    "name": group_name,
                    "price": yes_price,
                    "mid": mid,
                    "spread": spread,
                    "snapshot": snap,
                    "outcome": o,
                })
                total_implied_prob += yes_price
                break

    # 오버라운드 계산
    overround = total_implied_prob - 1.0 if total_implied_prob > 0 else 0

    for item in sorted(all_outcomes, key=lambda x: x["price"], reverse=True):
        pct = item["price"] * 100
        mid_pct = item["mid"] * 100
        dec_odds = 1.0 / item["price"] if item["price"] > 0 else 0

        lines.append(f"  {item['name']}")
        lines.append(f"    시장가: {pct:.1f}% (배당 {dec_odds:.2f}x)")
        lines.append(f"    {_bar(item['price'])} {pct:.1f}%")
        if item["mid"] != item["price"]:
            lines.append(f"    CLOB 중간값: {mid_pct:.1f}%")
        if item["spread"] and item["spread"] > 0:
            lines.append(f"    스프레드: {item['spread']*100:.2f}%")
        lines.append("")

    if is_event:
        lines.append(f"  📌 내재확률 합계: {total_implied_prob*100:.1f}% (오버라운드: {overround*100:+.1f}%)")
        # 공정확률 계산 (오버라운드 제거)
        lines.append(f"  📌 공정확률 (오버라운드 제거):")
        for item in sorted(all_outcomes, key=lambda x: x["price"], reverse=True):
            fair = item["price"] / total_implied_prob if total_implied_prob > 0 else item["price"]
            lines.append(f"    {item['name']}: {fair*100:.1f}%")
        lines.append("")

    # ══ 3) 외부 배당률 ══
    lines.append("## 3) 🌐 외부 배당률 비교")
    if ref_odds:
        lines.append("  [사용자 입력 참고 배당률]")
        for name, odds in ref_odds.items():
            prob = 1.0 / odds if odds > 0 else 0
            lines.append(f"  {name}: 배당 {odds:.2f}x (내재 {prob*100:.1f}%)")
    elif ext_odds:
        lines.append("  [The Odds API 외부 배당률]")
        for name, odds in ext_odds.items():
            prob = 1.0 / odds if odds > 0 else 0
            lines.append(f"  {name}: 배당 {odds:.2f}x (내재 {prob*100:.1f}%)")
    else:
        lines.append("  외부 배당률 없음 — CLOB 중간값 기반 분석")
    lines.append("")

    # ══ 4) 마켓 품질 ══
    lines.append("## 4) 🏦 마켟 품질")
    for snap in markets:
        liq = snap.liquidity or 0
        vol = snap.volume24hr or 0
        spreads = [o.spread for o in snap.outcomes if hasattr(o, 'spread') and o.spread]
        avg_spread = sum(spreads) / len(spreads) if spreads else None
        grade, grade_text = _grade_market(liq, vol, avg_spread)
        short_name = snap.title.split("?")[0].replace("Will ", "")[:30]
        lines.append(f"  [{short_name}] 등급: {grade} ({grade_text})")
        lines.append(f"    유동성: ${liq:,.0f} | 24시간 거래량: ${vol:,.0f}")
        if avg_spread:
            lines.append(f"    평균 스프레드: {avg_spread*100:.2f}%")
    lines.append("")

    # ══ 5) 투자 판단 ══
    lines.append("## 5) 💰 투자 판단")

    # 엣지 계산
    best_edge = -999
    best_outcome = None

    for item in all_outcomes:
        price = item["price"]
        mid = item["mid"]

        # 공정확률 결정
        if ref_odds:
            # 외부 배당률 기반
            matching_ref = None
            for ref_name, ref_val in ref_odds.items():
                if ref_name.lower() in item["name"].lower() or item["name"].lower() in ref_name.lower():
                    matching_ref = 1.0 / ref_val if ref_val > 0 else None
                    break
            fair = matching_ref if matching_ref else (price / total_implied_prob if total_implied_prob > 0 else price)
        elif ext_odds:
            matching_ext = None
            for ext_name, ext_val in ext_odds.items():
                if ext_name.lower() in item["name"].lower() or item["name"].lower() in ext_name.lower():
                    matching_ext = 1.0 / ext_val if ext_val > 0 else None
                    break
            fair = matching_ext if matching_ext else (price / total_implied_prob if total_implied_prob > 0 else price)
        elif is_event:
            # 이벤트 내 오버라운드 제거로 공정확률 추정
            fair = price / total_implied_prob if total_implied_prob > 0 else price
        else:
            # CLOB 중간값 활용
            fair = mid if mid != price else price

        # 비용 추정
        spread_cost = item["spread"] / 2 if item["spread"] else 0.005
        slippage = 0.005
        total_cost = spread_cost + slippage

        # 엣지 = 공정확률 - 시장가 - 비용
        edge = fair - price - total_cost
        ev = edge / price * 100 if price > 0 else 0

        # Kelly 계산
        kelly = 0
        if fair > price and price > 0:
            kelly = fractional_kelly_fraction(fair, price, 0.25)

        item["fair"] = fair
        item["edge"] = edge
        item["ev"] = ev
        item["kelly"] = kelly
        item["total_cost"] = total_cost

        if edge > best_edge:
            best_edge = edge
            best_outcome = item

    # 각 결과별 분석 표시
    for item in sorted(all_outcomes, key=lambda x: x["ev"], reverse=True):
        price_pct = item["price"] * 100
        fair_pct = item["fair"] * 100
        edge_pct = item["edge"] * 100
        ev_pct = item["ev"]
        cost_pct = item["total_cost"] * 100

        # 판정
        if item["ev"] > 3:
            verdict = "🟢 강력 추천"
        elif item["ev"] > 1:
            verdict = "🟡 추천"
        elif item["ev"] > -1:
            verdict = "⚪ 중립"
        elif item["ev"] > -3:
            verdict = "🟠 비추천"
        else:
            verdict = "🔴 패스"

        lines.append(f"  {item['name']}: {verdict}")
        lines.append(f"    시장가: {price_pct:.1f}% → 공정확률: {fair_pct:.1f}%")
        lines.append(f"    역지: {edge_pct:+.2f}% | EV: {ev_pct:+.2f}% | 비용: {cost_pct:.2f}%")
        if item["kelly"] > 0:
            lines.append(f"    켈리 비율 (¼): {item['kelly']*100:.1f}% 배팅 권장")
        lines.append("")

    # ══ 6) 최종 요약 ══
    lines.append("## 6) 📋 최종 요약")
    if best_outcome and best_outcome["ev"] > 1:
        lines.append(f"  ✅ 베팅 추천: {best_outcome['name']}")
        lines.append(f"     EV: {best_outcome['ev']:+.2f}% | 켈리: {best_outcome['kelly']*100:.1f}%")
    elif best_outcome and best_outcome["ev"] > -1:
        lines.append(f"  ⚖️ 중립 — 약간의 기회가 있을 수 있음")
        lines.append(f"     최선: {best_outcome['name']} (EV: {best_outcome['ev']:+.2f}%)")
    else:
        lines.append(f"  ❌ 현재 가치 베팅 없음")
        if best_outcome:
            lines.append(f"     최선: {best_outcome['name']} (EV: {best_outcome['ev']:+.2f}%)")

    if not ref_odds and not ext_odds:
        lines.append("")
        lines.append("  💡 팁: 참고 배당률을 입력하메 더 정확한 엣지 분석이 가능합니다")
        lines.append("     예: 맨시티: 1.05, 무승부: 12.0, 살포드: 40.0")

    return "\n".join(lines)
