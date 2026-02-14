"""Polybet AI 분석 모듈 v3 – 확률 수치 추출 + 텍스트 분석 통합"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Optional


async def ai_research(event_title: str, api_key: str, markets_summary: str = "") -> dict:
    """Claude API로 경기 분석 + 확률 수치 추출

    Returns:
        {
            "text": "분석 텍스트...",
            "probabilities": {"팀A 승리": 0.65, "팀B 승리": 0.25, "무승부": 0.10},
            "confidence": 0.7,  # AI 자신감 (0~1)
            "factors": ["+팀A 최근 5연승", "-팀B 핵심선수 부상", ...]
        }
    """
    try:
        import anthropic
    except ImportError:
        return {"text": "(anthropic 패키지 미설치 – pip install anthropic 실행 필요)", "probabilities": {}, "confidence": 0, "factors": []}

    if not api_key or not api_key.strip():
        return {"text": "", "probabilities": {}, "confidence": 0, "factors": []}

    prompt = f"""당신은 스포츠 베팅 전문 분석가입니다. 다음 이벤트에 대해 웹에서 최신 정보를 검색하고 분석해주세요.

이벤트: {event_title}

{f"현재 마켓 데이터:{chr(10)}{markets_summary}" if markets_summary else ""}

반드시 조사할 항목:
1. 최근 폼 (최근 3~5경기 성적)
2. 부상/결장/밴 정보
3. 상대 전적 (Head-to-Head)
4. 홈/원정 또는 대회 성적
5. 전문가/커뮤니티 예측
6. 특이사항 (징크스, 연승/연패, 메타 변화 등)

분석 후 반드시 아래 JSON 형식으로 마지막에 출력하세요 (```json 블록으로 감싸주세요):

```json
{{
  "analysis": "핵심 분석 요약 (3~5줄)",
  "probabilities": {{
    "결과1 이름": 확률(0~1),
    "결과2 이름": 확률(0~1)
  }},
  "confidence": AI분석_자신감(0.0~1.0),
  "factors_positive": ["+유리한 요소1", "+유리한 요소2"],
  "factors_negative": ["-불리한 요소1", "-불리한 요소2"],
  "value_picks": ["가치베팅 추천1", "가치베팅 추천2"],
  "risk_warnings": ["리스크1", "리스크2"]
}}
```

중요:
- probabilities의 키는 마켓 데이터에 나온 이름과 최대한 일치시켜주세요
- 확률 합계는 반드시 1.0이 되어야 합니다
- confidence는 정보가 충분하면 0.7~0.9, 불확실하면 0.3~0.5
- 반드시 JSON 블록을 포함해주세요"""

    client = anthropic.Anthropic(api_key=api_key.strip())

    # 1차: 웹 검색 포함
    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=3000,
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
        full_text = "\n".join(text_parts) if text_parts else ""
    except anthropic.AuthenticationError:
        return {
            "text": "(인증 실패 – API 키 확인 / console.anthropic.com/settings/billing 에서 결제 등록 확인)",
            "probabilities": {}, "confidence": 0, "factors": []
        }
    except anthropic.PermissionError:
        return {
            "text": "(권한 오류 – API 키 권한 확인 필요)",
            "probabilities": {}, "confidence": 0, "factors": []
        }
    except (anthropic.BadRequestError, Exception) as e:
        # 웹 검색 실패 시 폴백
        err = str(e)
        if "web_search" in err.lower() or "tool" in err.lower() or "BadRequest" in err:
            try:
                response = client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=3000,
                    messages=[{"role": "user", "content": prompt + "\n\n(웹 검색 불가 – 기존 지식으로 분석)"}]
                )
                text_parts = []
                for block in response.content:
                    if hasattr(block, 'text'):
                        text_parts.append(block.text)
                full_text = "[웹 검색 미지원 – 기존 지식 기반]\n\n" + ("\n".join(text_parts) if text_parts else "")
            except anthropic.AuthenticationError:
                return {
                    "text": "(인증 실패 – API 키/결제 확인)",
                    "probabilities": {}, "confidence": 0, "factors": []
                }
            except Exception as e2:
                return {
                    "text": f"(AI 분석 오류: {e2})",
                    "probabilities": {}, "confidence": 0, "factors": []
                }
        elif "authentication" in err.lower() or "api_key" in err.lower():
            return {
                "text": "(인증 실패 – API 키/결제 확인)",
                "probabilities": {}, "confidence": 0, "factors": []
            }
        else:
            return {
                "text": f"(AI 분석 오류: {err})",
                "probabilities": {}, "confidence": 0, "factors": []
            }

    # JSON 파싱
    result = {
        "text": full_text,
        "probabilities": {},
        "confidence": 0.5,
        "factors": []
    }

    # ```json ``` 블록 추출
    json_match = re.search(r'```json\s*(\{.*?\})\s*```', full_text, re.DOTALL)
    if not json_match:
        json_match = re.search(r'(\{[^{}]*"probabilities"[^{}]*\{[^{}]*\}[^{}]*\})', full_text, re.DOTALL)

    if json_match:
        try:
            data = json.loads(json_match.group(1))
            if "probabilities" in data and isinstance(data["probabilities"], dict):
                # 확률 정규화 (합계 1.0)
                probs = {}
                for k, v in data["probabilities"].items():
                    if isinstance(v, (int, float)) and v > 0:
                        probs[k] = float(v)
                total = sum(probs.values())
                if total > 0:
                    result["probabilities"] = {k: v/total for k, v in probs.items()}

            if "confidence" in data:
                conf = float(data["confidence"])
                result["confidence"] = max(0.0, min(1.0, conf))

            factors = []
            for f in data.get("factors_positive", []):
                factors.append(f if f.startswith("+") else f"+{f}")
            for f in data.get("factors_negative", []):
                factors.append(f if f.startswith("-") else f"-{f}")
            result["factors"] = factors

            if data.get("value_picks"):
                result["value_picks"] = data["value_picks"]
            if data.get("risk_warnings"):
                result["risk_warnings"] = data["risk_warnings"]
            if data.get("analysis"):
                # 분석 텍스트에 요약 추가
                result["analysis_summary"] = data["analysis"]
        except (json.JSONDecodeError, ValueError, KeyError):
            pass

    return result
