"""문제·정답 검증 서비스.

강의노트 본문(SOURCE) 과 일반(공식) 지식 두 갈래로 정답을 재검토한다.
"""
from __future__ import annotations

import json
from typing import Any

from .llm import LLMUnavailableError, call_claude, parse_json_block


SYSTEM = """당신은 한국 자격시험 검수자입니다. 사용자가 의심하는 문제·정답에 대해 두 갈래로 검토해 JSON 만 반환합니다.

규칙:
1. NOTE_VIEW: 주어진 NOTE_SOURCE(강의노트 본문) 와 정답이 일치하는지 판단.
2. OFFICIAL_VIEW: 한국 자격시험 표준/공식 자료에 비추어 정답이 일반적으로 맞는지 판단.
   - 모르거나 불확실하면 "uncertain" 으로.
   - 가능하면 공식 출처(예: q-net.or.kr, kisa.or.kr)에서 통용되는 정답·정의를 한국어로 짧게.
3. verdict:
   - "ok"       : 두 관점 모두 정답과 일치 (안심)
   - "warning"  : 한쪽만 일치 또는 한쪽이 uncertain
   - "conflict" : 한쪽 또는 양쪽이 정답과 충돌
4. suggested_answer: 검수 결과 더 적절하다고 판단되는 정답을 표기.
   - mcq 면 0-based 인덱스 또는 보기 텍스트.
   - short 면 모범답안 한국어 문장.
   - 기존 정답이 맞다고 판단되면 그대로.
5. confidence 는 0.0~1.0 신뢰도.
6. 응답은 JSON 객체만:
{
  "verdict": "ok|warning|conflict",
  "confidence": 0.85,
  "note_view": {"consistent": true|false|null, "explanation": "..."},
  "official_view": {"consistent": true|false|null, "explanation": "...", "source_hint": "..."},
  "suggested_answer": <원형 그대로>,
  "summary": "1~2문장 종합 의견"
}
"""


def verify_question(
    *,
    stem: str,
    qtype: str,
    choices: list[str] | None,
    correct_answer: Any,
    rationale: str | None,
    note_source: str,
) -> dict | None:
    user_msg = (
        f"QUESTION: {stem}\n"
        f"TYPE: {qtype}\n"
        f"CHOICES: {json.dumps(choices, ensure_ascii=False) if choices else 'null'}\n"
        f"STORED_CORRECT_ANSWER: {json.dumps(correct_answer, ensure_ascii=False)}\n"
        f"STORED_RATIONALE: {rationale or '-'}\n\n"
        f"NOTE_SOURCE (강의노트 발췌):\n---\n{note_source[:8000]}\n---\n\n"
        "위 정보를 바탕으로 두 갈래(NOTE_VIEW + OFFICIAL_VIEW) 검토 후 JSON 객체만 반환하세요."
    )
    try:
        text = call_claude(SYSTEM, user_msg, max_tokens=1200, temperature=0.0)
    except LLMUnavailableError as e:
        raise
    except Exception as e:
        return None
    obj = parse_json_block(text)
    if not isinstance(obj, dict):
        return None

    # 정규화
    verdict = obj.get("verdict")
    if verdict not in ("ok", "warning", "conflict"):
        verdict = "warning"
    try:
        confidence = float(obj.get("confidence") or 0.0)
        confidence = max(0.0, min(1.0, confidence))
    except (TypeError, ValueError):
        confidence = 0.0

    return {
        "verdict": verdict,
        "confidence": round(confidence, 2),
        "note_view": obj.get("note_view") or {},
        "official_view": obj.get("official_view") or {},
        "suggested_answer": obj.get("suggested_answer"),
        "summary": (obj.get("summary") or "").strip(),
    }
