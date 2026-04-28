"""정답 해설을 정확·간결하게 요약하고 레퍼런스 인용을 추출."""
from __future__ import annotations

import json
from typing import Any

from .llm import LLMUnavailableError, call_claude, parse_json_block


SYSTEM = """당신은 한국어 시험 해설가입니다. 다음 입력을 받아 JSON 만 반환합니다.
- QUESTION: 문제 본문
- CHOICES: 객관식 보기 (없으면 null)
- CORRECT_ANSWER: 정답 (mcq 는 0-based 인덱스, short 는 모범답안)
- SOURCE_CHUNKS: 관련 강의노트 발췌 (chunk_id 포함)

규칙:
1. rationale 은 한국어 2~4문장. 왜 그 보기가 정답이고 다른 보기가 왜 틀린지 압축.
2. quotes 는 SOURCE_CHUNKS 에서 정답 근거가 되는 부분을 **있는 그대로 한 문장씩** 발췌.
   - 한 인용은 60자 이내로 자르거나 '…' 로 축약.
   - 같은 chunk 에서 여러 문장이 필요하면 하나의 quote 안에 '… ' 로 이어붙임.
   - quote 텍스트는 원문 그대로(번역·재진술 금지). 원문에 없는 말은 quote 에 넣지 말 것.
3. 최소 1개, 최대 3개의 quote 를 반환. SOURCE_CHUNKS 에 근거가 부족하면 빈 배열도 허용.

출력 (순수 JSON 만):
{
  "rationale": "...",
  "quotes": [
    {"chunk_id": 12, "quote": "…발췌 문장…"},
    ...
  ]
}
"""


def summarize_explanation(
    *,
    stem: str,
    qtype: str,
    choices: list[str] | None,
    correct_answer: Any,
    raw_refs: list[dict],
) -> dict | None:
    """LLM 으로 해설 요약 + 인용문 추출. 실패 시 None."""
    if not raw_refs:
        return None

    src_block = []
    for r in raw_refs:
        cid = r.get("chunk_id")
        sn = (r.get("snippet") or "").strip()
        src_block.append(f"[chunk_id={cid}]\n{sn}")
    user_msg = (
        f"QUESTION: {stem}\n"
        f"TYPE: {qtype}\n"
        f"CHOICES: {json.dumps(choices, ensure_ascii=False) if choices else 'null'}\n"
        f"CORRECT_ANSWER: {json.dumps(correct_answer, ensure_ascii=False)}\n\n"
        f"SOURCE_CHUNKS:\n---\n" + "\n\n".join(src_block) + "\n---\n\n"
        "위 입력으로 JSON 한 객체만 반환하세요."
    )

    try:
        text = call_claude(SYSTEM, user_msg, max_tokens=900, temperature=0.0)
    except LLMUnavailableError:
        return None
    except Exception:
        return None

    obj = parse_json_block(text)
    if not isinstance(obj, dict):
        return None

    rationale = (obj.get("rationale") or "").strip()
    quotes_raw = obj.get("quotes") or []
    quotes: list[dict] = []
    if isinstance(quotes_raw, list):
        for q in quotes_raw[:3]:
            if not isinstance(q, dict):
                continue
            cid = q.get("chunk_id")
            qt = (q.get("quote") or "").strip()
            if not qt:
                continue
            try:
                cid = int(cid) if cid is not None else None
            except (TypeError, ValueError):
                cid = None
            # 60자 초과면 자르고 '…' 추가
            if len(qt) > 60:
                qt = qt[:60].rstrip() + "…"
            quotes.append({"chunk_id": cid, "quote": qt})

    if not rationale and not quotes:
        return None
    return {"rationale": rationale, "quotes": quotes}
