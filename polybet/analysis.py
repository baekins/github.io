"""Polybet – 스포츠 베팅 분석 엔진 v3 (모든 마켓 타입 지원)"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import timezone
from zoneinfo import ZoneInfo

from .clients import ClobClient, GammaClient, parse_market_payload
from .config import SETTINGS
from .costs import estimate_cost_for_outcome
from .geoblock import geoblock_status_message
from .math_utils import fractional_kelly_fraction
from .models import Candidate, MarketSnapshot
from .parsing import extract_slug, parse_reference_odds

try:
    from .odds_api import fetch_external_odds
except ImportError:
    fetch_external_odds = None

SEOUL = ZoneInfo("Asia/Seoul")


# ── 유틸 ──

def _fmt_dt(dt):
    if dt is None:
        return "정보 없음"
    return dt.astimezone(SEOUL).strftime("%Y-%m-%d %H:%M KST")


def _bar(ratio, width=20):
    filled = int(ratio * width)
    return "\u2588" * filled + "\u2591" * (width - filled)


def _dec_to_american(dec_odds):
    """소수 배당률 -> 미국식 배당률"""
    if dec_odds >= 2.0:
        return f"+{int((dec_odds - 1) * 100)}"
    elif dec_odds > 1.0:
        return f"-{int(100 / (dec_odds - 1))}"
    return "N/A"


def _classify_market(question: str, group_item_title: str) -> str:
    """마켓을 분류: moneyline, handicap, total, prop, game_winner"""
    q = question.lower()
    g = group_item_title.lower()

    if g == "match winner" or ("winner" in g and "game" not in g):
        return "moneyline"
    if "handicap" in q or "handicap" in g:
        return "handicap"
    if "total" in q or "o/u" in q or "over/under" in q:
        return "total"
    if ("will" in q and "win" in q) or ("winner" in g):
        if "game" in q or "map" in q or "game" in g:
            return "game_winner"
        return "moneyline"
    if "kill" in q or "first" in q or "tower" in q or "baron" in q or "dragon" in q:
        return "prop"
    # 축구 머니라인 (Will X win on DATE?)
    if "will" in q and ("win" in q or "end in a draw" in q):
        return "moneyline"
    return "other"


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
    return "D", "낮음"


# ── 데이터 수집 ──

async def _fetch_event_markets(gamma: GammaClient, slug: str):
    """이벤트 slug로 모든 마켓 가져오기"""
    try:
        raw = await gamma.fetch_event_by_slug(slug)
        if raw and isinstance(raw, dict):
            markets_raw = raw.get("markets", [])
            if markets_raw:
                event_title = raw.get("title", "Unknown Event")
                markets = []
                for m in markets_raw:
                    snap = parse_market_payload(m)
                    # raw 데이터 보존 (groupItemTitle 등)
                    snap.raw = m if isinstance(m, dict) else {}
                    markets.append(snap)
                return event_title, markets
    except Exception:
        pass
    return None, []


async def _fetch_single_market(gamma: GammaClient, slug: str):
    try:
        raw = await gamma.fetch_market_by_slug(slug)
        if raw:
            snap = parse_market_payload(raw)
            snap.raw = raw if isinstance(raw, dict) else {}
            return snap
    except Exception:
        pass
    return None


# ── 분석 엔진 ──

async def analyze(text: str, ref_odds_text: str = "") -> str:
    """메인 분석 함수 - 모든 마켗 타입 지원"""
    geo_msg = geoblock_status_message()
    slug_type, slug = extract_slug(text)
    gamma = GammaClient()

    event_title = None
    markets = []

    # 1) 데이터 수집
    if slug_type and slug:
        event_title, markets = await _fetch_event_markets(gamma, slug)
        if not markets:
            snap = await _fetch_single_market(gamma, slug)
            if snap:
                event_title = snap.title
                markets = [snap]
        if not markets:
            try:
                search_result = await gamma.search(slug)
                items = search_result.get("markets", []) + search_result.get("events", [])
                if items:
                    first = items[0]
                    if first.get("markets"):
                        event_title = first.get("title", slug)
                        for m in first["markets"]:
                            snap = parse_market_payload(m)
                            snap.raw = m if isinstance(m, dict) else {}
                            markets.append(snap)
                    else:
                        snap = parse_market_payload(first)
                        snap.raw = first if isinstance(first, dict) else {}
                        event_title = snap.title
                        markets = [snap]
            except Exception:
                pass
    else:
        try:
            search_result = await gamma.search(text)
            items = search_result.get("markets", []) + search_result.get("events", [])
            if items:
                first = items[0]
                if first.get("markets"):
                    event_title = first.get("title", text)
                    for m in first["markets"]:
                        snap = parse_market_payload(m)
                        snap.raw = m if isinstance(m, dict) else {}
                        markets.append(snap)
                else:
                    snap = parse_market_payload(first)
                    snap.raw = first if isinstance(first, dict) else {}
                    event_title = snap.title
                    markets = [snap]
        except Exception:
            pass

    if not markets:
        return f"오류: '{text}'에 대한 마켓을 찾을 수 없습니다."

    # 2) 마켟 분류
    classified = {"moneyline": [], "handicap": [], "total": [],
                  "game_winner": [], "prop": [], "other": []}

    for snap in markets:
        raw = snap.raw if isinstance(snap.raw, dict) else {}
        question = raw.get("question", "") or snap.title
        git = raw.get("groupItemTitle", "") or ""
        mtype = _classify_market(question, git)
        classified[mtype].append((snap, raw, question, git))

    # 3) 외부 배당률
    ref_odds = parse_reference_odds(ref_odds_text) if ref_odds_text else {}

    # 4) 결과 구성
    lines = []
    lines.append(f"# {event_title or markets[0].title}")
    lines.append("")

    # ══ 1) 이벤트 정보 ══
    lines.append("## 1) 📊 이벤트 정보")
    total_markets = len(markets)
    ml_count = len(classified["moneyline"])
    hc_count = len(classified["handicap"])
    tot_count = len(classified["total"])
    prop_count = len(classified["prop"]) + len(classified["game_winner"]) + len(classified["other"])
    lines.append(f"  총 마켓: {total_markets}개 (머니라인 {ml_count} | 핸디캡 {hc_count} | 토탈 {tot_count} | 기타 {prop_count})")

    for snap in markets[:1]:
        status = "🟢 활성" if snap.active else "🔴 비활성"
        state = "마감됨" if snap.closed else "진행중!#��
        lines.append(f"  상태: {status} | {state}")
        lines.append(f"  시작: {_fmt_dt(snap.start_date)}")
    lines.append(f"  조회: {_fmt_dt(markets[0].fetched_at)}")
    if geo_msg:
        lines.append(f"  ⚠️ {geo_msg}")
    lines.append("")

    # ══ 2) 머니라인 (핵심) ══
    lines.append("## 2) 💰 머니라인 (Match Winner)")
    if classified["moneyline"]:
        for snap, raw, question, git in classified["moneyline"]:
            outcomes = snap.outcomes
            if not outcomes:
                continue

            lines.append(f"  [{git or question}]")
            lines.append("")

            for o in outcomes:
                price = o.price
                if price <= 0:
                    continue
                dec_odds = 1.0 / price
                amer = _dec_to_american(dec_odds)
                pct = price * 100
                lines.append(f"  {o.name}")
                lines.append(f"    확률: {pct:.1f}% | 배당: {dec_odds:.2f}x ({amer})")
                lines.append(f"    {_bar(price)} {pct:.1f}%")
                lines.append("")

            # 오버라운드
            total_prob = sum(o.price for o in outcomes if o.price > 0)
            overround = (total_prob - 1.0) * 100
            lines.append(f"  내재확률 합계: {total_prob*100:.1f}% (오버라운드: {overround:+.1f}%)")

            # 공정확률
            if total_prob > 0:
                lines.append("  공정확률 (오버라운드 제거):")
                for o in outcomes:
                    if o.price > 0:
                        fair = o.price / total_prob
                        fair_odds = 1.0 / fair
                        lines.append(f"    {o.name}: {fair*100:.1f}% (공정배당 {fair_odds:.2f}x)")
            lines.append("")
    else:
        lines.append("  머니라인 마켓 없음")
        # 축구 등 Yes/No 기반 이벤트인지 확인
        yes_markets = []
        for snap, raw, question, git in (classified.get("other", []) + classified.get("game_winner", [])):
            for o in snap.outcomes:
                if o.name.lower() == "yes" and o.price > 0:
                    label = git or question.replace("Will ", "").split("?")[0]
                    yes_markets.append((label, o.price))
                    break
        if yes_markets:
            lines.append("  [Yes/No 기반 이벤트 결과]")
            total_prob = sum(p for _, p in yes_markets)
            for label, price in sorted(yes_markets, key=lambda x: x[1], reverse=True):
                dec_odds = 1.0 / price
                amer = _dec_to_american(dec_odds)
                lines.append(f"  {label}: {price*100:.1f}% | 배당 {dec_odds:.2f}x ({amer})")
                lines.append(f"    {_bar(price)} {price*100:.1f}%")
            overround = (total_prob - 1.0) * 100
            lines.append(f"  내재확률 함3ᠠ: {total_prob*100:.1f}% (오버라운드: {overround:+.1f}%)")
            if total_prob > 0:
                lines.append("  공정확률:")
                for label, price in sorted(yes_markets, key=lambda x: x[1], reverse=True):
                    fair = price / total_prob
                    lines.append(f"    {label}: {fair*100:.1f}%")
        lines.append("")

    # ══ 3) 함디캡 ══
    if classified["handicap"]:
        lines.append("## 3) 📐 함디캡")
        for snap, raw, question, git in classified["handicap"]:
            lines.append(f"  [{git or question}]")
            for o in snap.outcomes:
                if o.price > 0:
                    dec_odds = 1.0 / o.price
                    amer = _dec_to_american(dec_odds)
                    lines.append(f"    {o.name}: {o.price*100:.1f}% | 배당 {dec_odds:.2f}x ({amer})")
            lines.append("")

    # ══ 4) 토탈 (오버/언더) ══
    if classified["total"]:
        lines.append("## 4) 📊 토탈 (오벘-/언더)")
        for snap, raw, question, git in classified["total"]:
            lines.append(f"  [{git or question}]")
            for o in snap.outcomes:
                if o.price > 0:
                    dec_odds = 1.0 / o.price
                    amer = _dec_to_american(dec_odds)
                    lines.append(f"    {o.name}: {o.price*100:.1f}% | 배당 {dec_odds:.2f}x ({amer})")
            lines.append("")

    # ══ 5) 외부 배당률 비교 ══
    lines.append("## 5) 🌐 외부 배당률 비교")
    if ref_odds:
        lines.append("  [사용자 입력 참고 배당률]")
        for name, odds in ref_odds.items():
            prob = 1.0 / odds if odds > 0 else 0
            lines.append(f"  {name}: 배당 {odds:.2f}x (내재 {prob*100:.1f}%)")
    else:
        lines.append("  외부 배당률 없음")
        lines.append("  💡 참고 배당률 입력 시 더 정확한 엣지 분석 가능")
        lines.append("     예) OG: 8.5, Team Liquid: 1.08")
    lines.append("")

    # ══ 6) 투자 판단 ══
    lines.append("## 6) 💰 투자 판단")

    # 머니라인 기반 분석
    ml_outcomes = []
    for snap, raw, question, git in classified["moneyline"]:
        total_prob = sum(o.price for o in snap.outcomes if o.price > 0)
        for o in snap.outcomes:
            if o.price <= 0:
                continue
            fair = o.price / total_prob if total_prob > 0 else o.price

            # 외부 배당률 매츭
            if ref_odds:
                for ref_name, ref_val in ref_odds.items():
                    if ref_name.lower() in o.name.lower() or o.name.lower() in ref_name.lower():
                        fair = 1.0 / ref_val if ref_val > 0 else fair
                        break

            spread_cost = 0.005
            slippage = 0.005
            total_cost = spread_cost + slippage
            edge = fair - o.price - total_cost
            ev = edge / o.price * 100 if o.price > 0 else 0
            kelly = 0
            if fair > o.price and o.price > 0:
                kelly = fractional_kelly_fraction(fair, o.price, 0.25)

            ml_outcomes.append({
                "name": o.name, "price": o.price, "fair": fair,
                "edge": edge, "ev": ev, "kelly": kelly, "cost": total_cost
            })

    # Yes/No 기반 (축구 등) - 머니라인이 없을 때
    if not ml_outcomes:
        for snap, raw, question, git in (classified.get("other", []) + classified.get("game_winner", [])):
            for o in snap.outcomes:
                if o.name.lower() == "yes" and o.price > 0:
                    label = git or question.replace("Will ", "").split("?")[0]
                    # 같은 타책의 모든 yes를 모아서 total_prob 계산
                    yes_prices = []
                    for s2, r2, q2, g2 in (classified.get("other", []) + classified.get("game_winner", [])):
                        for o2 in s2.outcomes:
                            if o2.name.lower() == "yes" and o2.price > 0:
                                yes_prices.append(o2.price)
                    total_prob = sum(yes_prices) if yes_prices else 1.0
                    fair = o.price / total_prob if total_prob > 0 else o.price

                    if ref_odds:
                        for ref_name, ref_val in ref_odds.items():
                            if ref_name.lower() in label.lower() or label.lower() in ref_name.lower():
                                fair = 1.0 / ref_val if ref_val > 0 else fair
                                break

                    spread_cost = 0.005
                    slippage = 0.005
                    total_cost = spread_cost + slippage
                    edge = fair - o.price - total_cost
                    ev = edge / o.price * 100 if o.price > 0 else 0
                    kelly = 0
                    if fair > o.price and o.price > 0:
                        kelly = fractional_kelly_fraction(fair, o.price, 0.25)

                    ml_outcomes.append({
                        "name": label, "price": o.price, "fair": fair,
                        "edge": edge, "ev": ev, "kelly": kelly, "cost": total_cost
                    })
                    break

    best_outcome = None
    for item in sorted(ml_outcomes, key=lambda x: x["ev"], reverse=True):
        if best_outcome is None:
            best_outcome = item

        price_pct = item["price"] * 100
        fair_pct = item["fair"] * 100
        edge_pct = item["edge"] * 100
        ev_pct = item["ev"]
        cost_pct = item["cost"] * 100

        if ev_pct > 3:
            verdict = "🟢 *�🟢 추천"
        elif ev_pct > 1:
            verdict = "🟡 추천"
        elif ev_pct > -1:
            verdict = "⚪ 중+���"
        elif ev_pct > -3:
            verdict = "🟠 비추천"
        else:
            verdict = "🔴 패스"

        dec_odds = 1.0 / item["price"] if item["price"] > 0 else 0
        amer = _dec_to_american(dec_odds)

        lines.append(f"  {item['name']}: {verdict}")
        lines.append(f"    시장가: {price_pct:.1f}% ({dec_odds:.2f}x, {amer})")
        lines.append(f"    공정확률: {fair_pct:.1f}%")
        lines.append(f"    엣지: {edge_pct:+.2f}% | EV: {ev_pct:+.2f}% | 비용: {cost_pct:.2f}%")
        if item["kelly"] > 0:
            lines.append(f"    켈리 (1/4): {item['kelly']*100:.1f}% 배팅 권장")
        lines.append("")

    if not ml_outcomes:
        lines.append("  분석 가능한 머니라인 결과가 없습니다.")
        lines.append("")

    # ══ 7) 마켓 품질 ══
    lines.append("## 7) 🏦 마켓 품질")
    # 머니라인 마켟 품질만 표시
    quality_markets = classified["moneyline"] or classified.get("other", [])[:1]
    for snap, raw, question, git in quality_markets[:3]:
        liq = snap.liquidity or 0
        vol = snap.volume24hr or 0
        grade, grade_text = _grade_market(liq, vol, None)
        short = (git or question)[:40]
        lines.append(f"  [{short}] 등급: {grade} ({grade_text})")
        lines.append(f"    유동성: ${liq:,.0f} | 24h 거래량: ${vol:,.0f}")
    lines.append("")

    # ══ 8) 최종 요약 ══
    lines.append("## 8) 📋 최종 요약")
    if best_outcome and best_outcome["ev"] > 1:
        lines.append(f"  ✅ 베팅 추천: {best_outcome['name']}")
        dec_odds = 1.0 / best_outcome["price"] if best_outcome["price"] > 0 else 0
        lines.append(f"     배당: {dec_odds:.2f}x | EV: {best_outcome['ev']:+.2f}% | 켈리: {best_outcome['kelly']*100:.1f}%")
    elif best_outcome and best_outcome["ev"] > -1:
        lines.append(f"  ⚖️ 중립 — 미세한 기횈 가능")
        lines.append(f"     최선: {best_outcome['name']} (EV: {best_outcome['ev']:+.2f}%)")
    else:
        lines.append(f"  ❌ 현재 가치 베팅 없음")
        if best_outcome:
            lines.append(f"     최선: {best_outcome['name']} (EV: {best_outcome['ev']:+.2f}%)")

    if not ref_odds:
        lines.append("")
        lines.append("  💡 팁: 참고 배당률 입력으로 정확도 향상 가능")
        lines.append("     예) OG: 8.5, Team Liquid: 1.08")

    return "\n".join(lines)
