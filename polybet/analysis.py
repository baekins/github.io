"""Polybet – 스포츠 베팅 분석 엔진 v5 (마켓명 표시 + 실전 추천 기준)"""
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

try:
    from .ai_analysis import ai_research
except ImportError:
    ai_research = None

SEOUL = ZoneInfo("Asia/Seoul")


# ─── 유틸 ───

def _fmt_dt(dt):
    if dt is None:
        return "정보 없음"
    return dt.astimezone(SEOUL).strftime("%Y-%m-%d %H:%M KST")


def _bar(ratio, width=20):
    filled = int(ratio * width)
    return "█" * filled + "░" * (width - filled)


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
    if "will" in q and ("win" in q or "end in a draw" in q):
        return "moneyline"
    return "other"


def _market_label(question: str, git: str, outcome_name: str) -> str:
    """마켓+아웃컴에 대한 읽기 좋은 라벨 생성"""
    name_lower = outcome_name.lower()
    if name_lower not in ("yes", "no"):
        return outcome_name

    # Yes/No인 경우 질문에서 의미 추출
    q = question.strip()
    if q.endswith("?"):
        q = q[:-1]

    # "Will X win" 패턴
    m = re.match(r"(?i)will\s+(.+?)\s+(win|advance|qualify|beat)", q)
    if m:
        subject = m.group(1).strip()
        if name_lower == "yes":
            return f"{subject} 승리"
        else:
            return f"{subject} 패배/무"

    # "Will it end in a draw" 패턴
    if "draw" in q.lower():
        if name_lower == "yes":
            return "무승부"
        else:
            return "승패 결정"

    # 기본: 질문 + Yes/No
    short_q = q.replace("Will ", "").replace("will ", "")
    if len(short_q) > 30:
        short_q = short_q[:30] + "..."
    return f"{short_q} → {'예' if name_lower == 'yes' else '아니오'}"


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

async def analyze(text: str, ref_odds_text: str = "", api_key: str = "") -> str:
    """메인 분석 함수 - 모든 마켓 타입 + AI 실시간 분석 지원"""
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

    # 2) 마켓 분류
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

    # ═══ 1) 이벤트 정보 ═══
    lines.append("## 1) 📊 이벤트 정보")
    total_markets = len(markets)
    ml_count = len(classified["moneyline"])
    hc_count = len(classified["handicap"])
    tot_count = len(classified["total"])
    prop_count = len(classified["prop"]) + len(classified["game_winner"]) + len(classified["other"])
    lines.append(f"  총 마켓: {total_markets}개 (머니라인 {ml_count} | 핸디캡 {hc_count} | 토탈 {tot_count} | 기타 {prop_count})")

    for snap in markets[:1]:
        status = "🟢 활성" if snap.active else "🔴 비활성"
        state = "마감됨" if snap.closed else "진행중"
        lines.append(f"  상태: {status} | {state}")
        lines.append(f"  시작: {_fmt_dt(snap.start_date)}")
    lines.append(f"  조회: {_fmt_dt(markets[0].fetched_at)}")
    if geo_msg:
        lines.append(f"  ⚠️ {geo_msg}")
    lines.append("")

    # ═══ 2) 머니라인 (핵심) ═══
    lines.append("## 2) 💰 머니라인 (Match Winner)")
    if classified["moneyline"]:
        for snap, raw, question, git in classified["moneyline"]:
            outcomes = snap.outcomes
            if not outcomes:
                continue

            lines.append(f"  📌 {git or question}")
            lines.append("")

            for o in outcomes:
                price = o.price
                if price <= 0:
                    continue
                dec_odds = 1.0 / price
                amer = _dec_to_american(dec_odds)
                pct = price * 100
                label = _market_label(question, git, o.name)
                lines.append(f"  {label}")
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
                        label = _market_label(question, git, o.name)
                        lines.append(f"    {label}: {fair*100:.1f}% (공정배당 {fair_odds:.2f}x)")
            lines.append("")
    else:
        # 머니라인 없음 - Yes/No 기반 이벤트
        yes_markets = []
        for snap, raw, question, git in (classified.get("other", []) + classified.get("game_winner", [])):
            for o in snap.outcomes:
                if o.name.lower() == "yes" and o.price > 0:
                    label = _market_label(question, git, o.name)
                    yes_markets.append((label, o.price, question, git))
                    break
        if yes_markets:
            lines.append("  📌 경기 결과 마켓")
            lines.append("")
            total_prob = sum(p for _, p, _, _ in yes_markets)
            for label, price, q, g in sorted(yes_markets, key=lambda x: x[1], reverse=True):
                dec_odds = 1.0 / price
                amer = _dec_to_american(dec_odds)
                lines.append(f"  {label}: {price*100:.1f}% | 배당 {dec_odds:.2f}x ({amer})")
                lines.append(f"    {_bar(price)} {price*100:.1f}%")
            overround = (total_prob - 1.0) * 100
            lines.append(f"\n  내재확률 합계: {total_prob*100:.1f}% (오버라운드: {overround:+.1f}%)")
            if total_prob > 0:
                lines.append("  공정확률:")
                for label, price, q, g in sorted(yes_markets, key=lambda x: x[1], reverse=True):
                    fair = price / total_prob
                    lines.append(f"    {label}: {fair*100:.1f}%")
        else:
            lines.append("  머니라인 마켓 없음")
        lines.append("")

    # ═══ 3) 핸디캡 ═══
    if classified["handicap"]:
        lines.append("## 3) 📐 핸디캡")
        for snap, raw, question, git in classified["handicap"]:
            lines.append(f"  📌 {git or question}")
            for o in snap.outcomes:
                if o.price > 0:
                    dec_odds = 1.0 / o.price
                    amer = _dec_to_american(dec_odds)
                    label = _market_label(question, git, o.name)
                    lines.append(f"    {label}: {o.price*100:.1f}% | 배당 {dec_odds:.2f}x ({amer})")
            lines.append("")

    # ═══ 4) 토탈 (오버/언더) ═══
    if classified["total"]:
        lines.append("## 4) 📊 토탈 (오버/언더)")
        for snap, raw, question, git in classified["total"]:
            lines.append(f"  📌 {git or question}")
            for o in snap.outcomes:
                if o.price > 0:
                    dec_odds = 1.0 / o.price
                    amer = _dec_to_american(dec_odds)
                    label = _market_label(question, git, o.name)
                    lines.append(f"    {label}: {o.price*100:.1f}% | 배당 {dec_odds:.2f}x ({amer})")
            lines.append("")

    # ═══ 5) AI 실시간 분석 ═══
    lines.append("## 5) 🤖 AI 실시간 분석")
    if api_key and ai_research:
        market_summary_parts = []
        for snap, raw, question, git in classified.get("moneyline", []):
            for o in snap.outcomes:
                if o.price > 0:
                    label = _market_label(question, git, o.name)
                    market_summary_parts.append(f"  {label}: {o.price*100:.1f}%")
        if not market_summary_parts:
            for snap, raw, question, git in (classified.get("other", []) + classified.get("game_winner", [])):
                for o in snap.outcomes:
                    if o.name.lower() == "yes" and o.price > 0:
                        label = _market_label(question, git, o.name)
                        market_summary_parts.append(f"  {label}: {o.price*100:.1f}%")
        markets_summary = "\n".join(market_summary_parts) if market_summary_parts else ""

        try:
            ai_result = await ai_research(
                event_title or markets[0].title,
                api_key,
                markets_summary
            )
            if ai_result:
                for line in ai_result.split("\n"):
                    lines.append(f"  {line}")
            else:
                lines.append("  AI 분석 결과 없음")
        except Exception as e:
            lines.append(f"  AI 분석 오류: {e}")
    elif not api_key:
        lines.append("  Claude API 키 미입력")
        lines.append("  💡 API 키 입력 시 실시간 웹 검색으로 다음 정보를 자동 분석합니다:")
        lines.append("     - 선수 부상/결장 정보")
        lines.append("     - 최근 팀 컨디션 (최근 5경기)")
        lines.append("     - 상대 전적 (H2H)")
        lines.append("     - 전문가 예측 및 커뮤니티 의견")
        lines.append("     - 징크스, 메타 변화 등 특이사항")
    else:
        lines.append("  (anthropic 패키지 미설치)")
    lines.append("")

    # ═══ 6) 외부 배당률 비교 ═══
    lines.append("## 6) 🌐 외부 배당률 비교")
    if ref_odds:
        lines.append("  [사용자 입력 참고 배당률]")
        for name, odds in ref_odds.items():
            prob = 1.0 / odds if odds > 0 else 0
            lines.append(f"  {name}: 배당 {odds:.2f}x (내재 {prob*100:.1f}%)")
    else:
        lines.append("  외부 배당률 없음")
        lines.append("  💡 참고 배당률 입력 시 더 정확한 웃지 분석 가능")
        lines.append("     예) OG: 8.5, Team Liquid: 1.08")
    lines.append("")

    # ═══ 7) 투자 판단 ═══
    lines.append("## 7) 💰 투자 판단")

    all_outcomes = []

    # 머니라인 기반
    for snap, raw, question, git in classified["moneyline"]:
        total_prob = sum(o.price for o in snap.outcomes if o.price > 0)
        for o in snap.outcomes:
            if o.price <= 0:
                continue
            label = _market_label(question, git, o.name)
            fair = o.price / total_prob if total_prob > 0 else o.price

            # 외부 배당률 매칭
            matched_ref = False
            if ref_odds:
                for ref_name, ref_val in ref_odds.items():
                    if ref_name.lower() in label.lower() or label.lower() in ref_name.lower() or ref_name.lower() in o.name.lower():
                        fair = 1.0 / ref_val if ref_val > 0 else fair
                        matched_ref = True
                        break

            # 비용: Polymarket은 수수료 없음, 스프레드만
            spread_cost = 0.002
            edge = fair - o.price - spread_cost
            ev = edge / o.price * 100 if o.price > 0 else 0

            # 오버라운드 보너스: 오버라운드가 크면 공정확률과 시장가 차이가 큼 = 기회
            overround_bonus = (total_prob - 1.0) * 50 if total_prob > 1.0 else 0

            kelly = 0
            if fair > o.price and o.price > 0:
                kelly = fractional_kelly_fraction(fair, o.price, 0.25)

            all_outcomes.append({
                "name": label, "price": o.price, "fair": fair,
                "edge": edge, "ev": ev + overround_bonus, "raw_ev": ev,
                "kelly": kelly, "cost": spread_cost,
                "type": "moneyline", "question": question,
                "matched_ref": matched_ref
            })

    # Yes/No 기반 (축구 등)
    if not any(item["type"] == "moneyline" for item in all_outcomes):
        yes_items = []
        for snap, raw, question, git in (classified.get("other", []) + classified.get("game_winner", [])):
            for o in snap.outcomes:
                if o.name.lower() == "yes" and o.price > 0:
                    label = _market_label(question, git, o.name)
                    yes_items.append((label, o.price, question, git, snap))
                    break

        total_prob = sum(p for _, p, _, _, _ in yes_items) if yes_items else 1.0
        for label, price, question, git, snap in yes_items:
            fair = price / total_prob if total_prob > 0 else price

            matched_ref = False
            if ref_odds:
                for ref_name, ref_val in ref_odds.items():
                    if ref_name.lower() in label.lower() or label.lower() in ref_name.lower():
                        fair = 1.0 / ref_val if ref_val > 0 else fair
                        matched_ref = True
                        break

            spread_cost = 0.002
            edge = fair - price - spread_cost
            ev = edge / price * 100 if price > 0 else 0
            overround_bonus = (total_prob - 1.0) * 50 if total_prob > 1.0 else 0

            kelly = 0
            if fair > price and price > 0:
                kelly = fractional_kelly_fraction(fair, price, 0.25)

            all_outcomes.append({
                "name": label, "price": price, "fair": fair,
                "edge": edge, "ev": ev + overround_bonus, "raw_ev": ev,
                "kelly": kelly, "cost": spread_cost,
                "type": "yes_no", "question": question,
                "matched_ref": matched_ref
            })

    # 핸디캡/토탈도 분석에 포함
    for mtype, cat_name in [("handicap", "핸디캡"), ("total", "토탈")]:
        for snap, raw, question, git in classified.get(mtype, []):
            total_prob = sum(o.price for o in snap.outcomes if o.price > 0)
            for o in snap.outcomes:
                if o.price <= 0:
                    continue
                label = _market_label(question, git, o.name)
                fair = o.price / total_prob if total_prob > 0 else o.price
                spread_cost = 0.002
                edge = fair - o.price - spread_cost
                ev = edge / o.price * 100 if o.price > 0 else 0
                overround_bonus = (total_prob - 1.0) * 50 if total_prob > 1.0 else 0
                kelly = 0
                if fair > o.price and o.price > 0:
                    kelly = fractional_kelly_fraction(fair, o.price, 0.25)

                all_outcomes.append({
                    "name": f"[{cat_name}] {label}",
                    "price": o.price, "fair": fair,
                    "edge": edge, "ev": ev + overround_bonus, "raw_ev": ev,
                    "kelly": kelly, "cost": spread_cost,
                    "type": mtype, "question": question,
                    "matched_ref": False
                })

    # 정렬: EV 높은 순
    all_outcomes.sort(key=lambda x: x["ev"], reverse=True)

    best_outcome = None
    shown = 0
    for item in all_outcomes:
        if shown >= 8:
            break
        if best_outcome is None:
            best_outcome = item

        price_pct = item["price"] * 100
        fair_pct = item["fair"] * 100
        edge_pct = item["edge"] * 100
        ev_pct = item["ev"]
        cost_pct = item["cost"] * 100

        # 실전 기준: 더 관대한 추천
        if ev_pct > 2:
            verdict = "🟢 강력 추천"
        elif ev_pct > 0.5:
            verdict = "🟡 추천"
        elif ev_pct > -0.5:
            verdict = "⚪ 소액 가능"
        elif ev_pct > -2:
            verdict = "🟠 비추천"
        else:
            verdict = "🔴 패스"

        dec_odds = 1.0 / item["price"] if item["price"] > 0 else 0
        amer = _dec_to_american(dec_odds)

        lines.append(f"  {item['name']}: {verdict}")
        lines.append(f"    시장가: {price_pct:.1f}% ({dec_odds:.2f}x, {amer})")
        lines.append(f"    공정확률: {fair_pct:.1f}%")
        lines.append(f"    웃지: {edge_pct:+.2f}% | EV: {ev_pct:+.2f}%")
        if item["kelly"] > 0:
            lines.append(f"    켈리 (1/4): {item['kelly']*100:.1f}% 배팅 권장")
        if item.get("matched_ref"):
            lines.append(f"    📋 외부 배당률 기반 분석")
        lines.append("")
        shown += 1

    if not all_outcomes:
        lines.append("  분석 가능한 결과가 없습니다.")
        lines.append("")

    # ═══ 8) 마켓 품질 ═══
    lines.append("## 8) 🏪 마켓 품질")
    quality_markets = classified["moneyline"] or classified.get("other", [])[:1]
    for snap, raw, question, git in quality_markets[:3]:
        liq = snap.liquidity or 0
        vol = snap.volume24hr or 0
        grade, grade_text = _grade_market(liq, vol, None)
        short = (git or question)[:40]
        lines.append(f"  [{short}] 등급: {grade} ({grade_text})")
        lines.append(f"    유동성: ${liq:,.0f} | 24h 거래량: ${vol:,.0f}")
    lines.append("")

    # ═══ 9) 최종 요약 ═══
    lines.append("## 9) 📋 최종 요약")
    if best_outcome and best_outcome["ev"] > 0.5:
        lines.append(f"  ✅ 베팅 추천: {best_outcome['name']}")
        dec_odds = 1.0 / best_outcome["price"] if best_outcome["price"] > 0 else 0
        lines.append(f"     배당: {dec_odds:.2f}x | EV: {best_outcome['ev']:+.2f}%")
        if best_outcome["kelly"] > 0:
            lines.append(f"     켈리 배팅: 자본의 {best_outcome['kelly']*100:.1f}%")
        # 추가 추천 찾기
        extra = [x for x in all_outcomes[1:] if x["ev"] > 0.5]
        if extra:
            lines.append(f"     + {len(extra)}개 추가 기회 있음")
    elif best_outcome and best_outcome["ev"] > -0.5:
        lines.append(f"  ⚖️ 소액 베팅 가능")
        lines.append(f"     최선: {best_outcome['name']} (EV: {best_outcome['ev']:+.2f}%)")
    else:
        lines.append(f"  ❌ 현재 가치 베팅 없음")
        if best_outcome:
            lines.append(f"     최선: {best_outcome['name']} (EV: {best_outcome['ev']:+.2f}%)")

    if not ref_odds:
        lines.append("")
        lines.append("  💡 팁: 외부 배당률 입력으로 더 정확한 웓지 분석!")
        lines.append("     예) 맨시티: 1.25, 살포드: 12.00, 무승부: 6.50")

    return "\n".join(lines)

