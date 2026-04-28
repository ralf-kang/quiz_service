"""Grade a user answer. MCQ is exact-index match; short uses LLM if available."""
from pathlib import Path

from .. import models
from .llm import LLMUnavailableError, call_claude, parse_json_block


PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "grade.txt"
SYSTEM_PROMPT = PROMPT_PATH.read_text(encoding="utf-8")


def _normalize(s: str) -> str:
    return "".join(c for c in (s or "").lower() if c.isalnum())


def grade_mcq(question: models.Question, user_answer) -> tuple[bool, str | None]:
    try:
        ua = int(user_answer)
    except (TypeError, ValueError):
        return False, "보기 번호를 숫자로 선택해 주세요."
    correct = ua == int(question.answer)
    fb = "정답입니다." if correct else "오답입니다."
    return correct, fb


def grade_short(question: models.Question, user_answer, source_snippets: list[str]) -> tuple[bool, str, str]:
    """Returns (correct, feedback, rationale)."""
    model_answer = str(question.answer or "")
    user_text = str(user_answer or "").strip()

    # Try LLM first
    try:
        source = "\n---\n".join(source_snippets)[:6000]
        user_msg = (
            f"QUESTION: {question.stem}\n\n"
            f"ANSWER (모범답안): {model_answer}\n\n"
            f"USER_ANSWER: {user_text}\n\n"
            f"SOURCE:\n{source}\n\n"
            "위 정보를 근거로 채점하고 JSON 객체만 반환하세요."
        )
        raw = call_claude(SYSTEM_PROMPT, user_msg, max_tokens=600, temperature=0.0)
        obj = parse_json_block(raw)
        if isinstance(obj, dict) and "correct" in obj:
            return (
                bool(obj["correct"]),
                str(obj.get("feedback") or ""),
                str(obj.get("rationale") or question.rationale or ""),
            )
    except LLMUnavailableError:
        pass
    except Exception:
        pass

    # Fallback heuristic: normalized substring
    a_norm = _normalize(model_answer)
    u_norm = _normalize(user_text)
    correct = bool(u_norm) and (u_norm in a_norm or a_norm in u_norm)
    fb = "정답입니다." if correct else "모범답안과 다릅니다. 핵심 개념을 다시 확인해 보세요."
    return correct, fb, question.rationale or ""
