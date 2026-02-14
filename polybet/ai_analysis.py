"""Polybet AI 분석 모듈 – Claude API + 웹 검색으로 실시간 정보 수집"""
from __future__ import annotations

import asyncio
from typing import Optional


async def ai_research(event_title: str, api_key: str, markets_summary: str = "") -> str:
    """Claude API로 경기 실시간 정보 조사 및 분석"""
    try:
        import anthropic
    except ImportError:
        return "(anthropic 패키지 미설치 – pip install anthropic 실행 필요)"

    if not api_key or not api_key.strip():
        return ""

    prompt = f"""당신은 스포츠 베팅 전문 분석가입니다. 다음 이벤트에 대해 웹에서 최신 정보를 검색하고 한국어로 분석해주세요.

이벤트: {event_title}

{f"현재 마켓 데이터:{chr(10)}{markets_summary}" if markets_summary else ""}

반드시 조사할 항목:
1. 최근 폼 (최근 3~5경기 성적)
2. 부상/결장/밴 정보 (핵심 선수 못 나오는지)
3. 상대 전적 (Head-to-Head 최근 결과)
4. 홈/원정 또는 대회 성적
5. 전문가/커뮤니티 예측 (승률 예상)
6. 특이사항 (징크스, 연승/연패, 메타 변화, 패치 영향 등)

마지막에 종합 판단을 내려주세요:
- 어느 쪽이 유리한지
- 마켓 가격 대비 가치가 있는 베팅이 있는지
- 주의해야 할 리스크

간결하고 핵심만 정리해주세요. 불필요한 서론은 빼고 바로 분석 결과를 출력하세요."""

    client = anthropic.Anthropic(api_key=api_key.strip())

    # 1차 시도: 웹 검색 도구 포함
    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2048,
            tools=[{
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": 5
            }],
            messages=[{"role": "user", "content": prompt}]
        )
        text_parts = []
        for block in response.content:
            if hasattr(block, 'text'):
                text_parts.append(block.text)
        if text_parts:
            return "\n".join(text_parts)
    except anthropic.AuthenticationError:
        return "(인증 실패 – API 키가 올바른지, 결제 정보가 등록되어 있는지 확인하세요)\n(https://console.anthropic.com/settings/billing 에서 확인)"
    except anthropic.PermissionError:
        return "(권한 오류 – 이 API 키로는 해당 기능을 사용할 수 없습니다)\n(API 키 권한을 확인하세요)"
    except anthropic.BadRequestError as e:
        # 웹 검색 미지원 시 일반 분석으로 폴백
        pass
    except anthropic.RateLimitError:
        return "(API 요청 한도 초과 – 잠시 후 다시 시도하세요)"
    except Exception as e:
        # 웹 검색 관련 오류면 폴백 시도
        err = str(e)
        if "web_search" in err.lower() or "tool" in err.lower():
            pass  # 아래 폴백으로 진행
        else:
            return f"(AI 분석 오류: {err})"

    # 2차 시도: 웹 검색 없이 일반 분석
    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt + "\n\n(웹 검색 없이 기존 지식으로 분석해주세요)"}]
        )
        text_parts = []
        for block in response.content:
            if hasattr(block, 'text'):
                text_parts.append(block.text)
        if text_parts:
            return "[웹 검색 미지원 – 기존 지식 기반 분석]\n\n" + "\n".join(text_parts)
    except anthropic.AuthenticationError:
        return "(인증 실패 – API 키가 올바른지, 결제 정보가 등록되어 있는지 확인하세요)\n(https://console.anthropic.com/settings/billing 에서 확인)"
    except Exception as e:
        return f"(AI 분석 오류: {e})"

    return "(AI 분석 결과 없음)"
