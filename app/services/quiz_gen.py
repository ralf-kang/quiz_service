"""Generate quiz questions for a document using Claude."""
import re
from pathlib import Path

from sqlalchemy.orm import Session

from .. import models
from ..config import settings
from .llm import call_claude, parse_json_block


PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "generate.txt"
SYSTEM_PROMPT = PROMPT_PATH.read_text(encoding="utf-8")

STYLE_PACKS_DIR = Path(__file__).resolve().parent.parent / "prompts" / "style_packs"


def _normalize_test_type(name: str) -> str:
    """파일 시스템에 안전한 형태로 정규화. 한글은 그대로 둔다."""
    return re.sub(r"[\s/\\]+", "-", (name or "").strip())


def _load_style_pack(test_type: str | None) -> tuple[str, str]:
    """test_type 에 매칭되는 기출 스타일 팩 로드.

    반환: (pack_name, pack_text). 매칭 실패 시 default.md 사용.
    매칭 우선순위: 정확 일치 → 대소문자 무시 일치 → 부분 매칭 → default.
    """
    if not STYLE_PACKS_DIR.exists():
        return ("default", "")

    target = _normalize_test_type(test_type or "")
    candidates = list(STYLE_PACKS_DIR.glob("*.md"))
    if not candidates:
        return ("default", "")

    # 정확 일치
    for p in candidates:
        if p.stem == target:
            return (p.stem, p.read_text(encoding="utf-8"))
    # 대소문자 무시
    lower = target.lower()
    for p in candidates:
        if p.stem.lower() == lower:
            return (p.stem, p.read_text(encoding="utf-8"))
    # 부분 매칭 (사용자 입력에 시험명이 포함된 경우) — target 이 비어있으면 스킵
    if target:
        for p in candidates:
            if p.stem == "default":
                continue
            if p.stem in target or target in p.stem:
                return (p.stem, p.read_text(encoding="utf-8"))

    default = STYLE_PACKS_DIR / "default.md"
    if default.exists():
        return ("default", default.read_text(encoding="utf-8"))
    return ("default", "")


def _has_pastexam_style(style: str) -> bool:
    if not style:
        return False
    parts = [s.strip() for s in style.split(",")]
    return any(s in ("기출", "기출문제", "past-exam") for s in parts)


_DIFF_RE = re.compile(r"난이도\s*[:：]\s*(\S+)")


def _detect_difficulty(extra: str | None) -> str | None:
    if not extra:
        return None
    m = _DIFF_RE.search(extra)
    return m.group(1).strip() if m else None


_KO_ENDING_RE = re.compile(
    r"(?:[.!?。！？]+|(?:습니다|입니다|됩니다|합니다|있다|없다|된다|한다|이다)\.?)"
    r"(?=\s|$|[\"'\)\]\}」』])"
)


def _count_sentences(text: str) -> int:
    """한국어 종결어미·문장부호·줄바꿈을 모두 인식해 문장 수를 센다.

    - 마침표/물음표/느낌표 + 한·일 종결부호
    - "~습니다.", "~합니다.", "~이다.", "~된다." 등 한국어 서술형 종결
    - 빈 줄(단락 경계) 도 한 문장 분리로 간주
    """
    if not text:
        return 0
    s = text.strip()
    if not s:
        return 0
    # 1) 한국어 종결어미·문장부호 매칭 횟수
    end_matches = len(_KO_ENDING_RE.findall(s))
    # 2) 줄바꿈/단락 분리(부호가 없는 짧은 행도 한 문장으로 인정)
    line_count = sum(1 for ln in re.split(r"\n+", s) if len(ln.strip()) > 1)
    # 둘 중 큰 값을 선택 (정의 나열식 노트도, 서사형 지문도 모두 합리적으로)
    return max(end_matches, line_count)


_SCENARIO_KEYWORDS = (
    "사건", "상황", "사례", "시나리오", "case", "보고", "감사",
    "프로젝트", "팀장", "담당자", "사고", "발생", "민원", "사용자",
    "고객", "회사", "부서", "공정", "현장", "공사", "공장", "지점",
    "지난", "최근", "지난해", "지난주", "오전", "오후", "당일", "다음 날",
    "A 사", "B 사", "A사", "B사", "갑 회사", "을 회사", "갑사", "을사",
    "A 팀", "B 팀", "A팀", "B팀", "K 사", "K사",
    "김 과장", "이 대리", "박 사원", "최 부장", "정 팀장",
    "검토 결과", "조사 결과", "조사한 결과", "확인 결과",
)


def _has_scenario(passage: str | None) -> bool:
    """passage 가 시나리오·서사 형태를 갖췄는지 키워드 기반 휴리스틱."""
    if not passage:
        return False
    p = passage
    return any(kw in p for kw in _SCENARIO_KEYWORDS)


def _gather_source(db: Session, document_id: int, max_chars: int = 12000) -> str:
    return _gather_multi_source(db, [document_id], max_chars=max_chars)


def _gather_multi_source(db: Session, document_ids: list[int], max_chars: int = 20000) -> str:
    """여러 문서의 청크를 doc 별 섹션으로 묶어 LLM 컨텍스트로 반환.

    각 섹션은 `## SOURCE: <filename> (doc_id=N)` 헤더로 구분되며,
    LLM 이 인용 시 어느 문서에서 가져왔는지 알 수 있게 한다.
    """
    if not document_ids:
        return ""
    # 각 문서에 균등 배분 (한 문서당 max_chars / N)
    per_doc_budget = max(2000, max_chars // max(1, len(document_ids)))
    sections: list[str] = []
    total = 0
    for did in document_ids:
        if total >= max_chars:
            break
        doc = db.get(models.Document, did)
        if doc is None:
            continue
        rows = (
            db.query(models.Chunk)
            .filter(models.Chunk.document_id == did)
            .order_by(models.Chunk.seq.asc())
            .all()
        )
        if not rows:
            continue
        section_parts: list[str] = [f"## SOURCE: {doc.filename} (doc_id={doc.id})\n"]
        section_total = 0
        budget = min(per_doc_budget, max_chars - total)
        for r in rows:
            s = r.content or ""
            if section_total + len(s) > budget:
                s = s[: max(0, budget - section_total)]
                if s:
                    section_parts.append(s)
                break
            section_parts.append(s)
            section_total += len(s)
        sections.append("\n".join(section_parts))
        total += section_total
    return "\n\n---\n\n".join(sections)


def _validate(items: list[dict], type_mix: dict) -> list[dict]:
    valid = []
    for it in items:
        t = (it.get("type") or "").strip().lower()
        if t not in ("mcq", "short"):
            continue
        stem = (it.get("stem") or "").strip()
        if not stem:
            continue
        if t == "mcq":
            choices = it.get("choices") or []
            if not (isinstance(choices, list) and len(choices) == 5):
                continue
            ans = it.get("answer")
            if not isinstance(ans, int) or not (0 <= ans < 5):
                continue
        else:
            ans = it.get("answer")
            if not isinstance(ans, str) or not ans.strip():
                continue
        # passage / group_id 는 선택 — 있으면 정규화
        passage = it.get("passage")
        if passage is not None and not isinstance(passage, str):
            it["passage"] = None
        gid = it.get("group_id")
        if gid is not None and not isinstance(gid, str):
            it["group_id"] = None
        valid.append(it)
    return valid


def _excluded_stems(db: Session, user_id: str, document_id: int, limit: int = 30) -> list[str]:
    return _excluded_stems_multi(db, user_id, [document_id], limit)


def _excluded_stems_multi(db: Session, user_id: str, document_ids: list[int], limit: int = 30) -> list[str]:
    if not document_ids:
        return []
    rows = (
        db.query(models.Question.stem)
        .filter(
            models.Question.owner_id == user_id,
            models.Question.document_id.in_(document_ids),
            models.Question.excluded.is_(True),
        )
        .order_by(models.Question.created_at.desc())
        .limit(limit)
        .all()
    )
    return [r[0] for r in rows if r and r[0]]


def generate_questions(
    db: Session,
    *,
    user_id: str,
    document_id: int | None = None,
    document_ids: list[int] | None = None,
    n: int,
    type_mix: dict,
    style: str,
    extra_instructions: str | None,
    topic_hint: str | None = None,
) -> list[models.Question]:
    # 다중 문서 우선, 없으면 단일 fall back
    doc_ids: list[int] = []
    if document_ids:
        doc_ids = [int(x) for x in document_ids]
    elif document_id is not None:
        doc_ids = [int(document_id)]
    if not doc_ids:
        raise ValueError("출제 대상 강의노트가 비어 있습니다.")

    # 사용자 소유 검증
    valid_docs = (
        db.query(models.Document)
        .filter(models.Document.id.in_(doc_ids), models.Document.owner_id == user_id)
        .all()
    )
    valid_ids = {d.id for d in valid_docs}
    doc_ids = [d for d in doc_ids if d in valid_ids]
    if not doc_ids:
        raise ValueError("선택한 강의노트를 찾을 수 없습니다.")

    primary_doc_id = doc_ids[0]
    source = _gather_multi_source(db, doc_ids)
    if not source.strip():
        raise ValueError("문서들에 추출된 텍스트가 없습니다. 다른 파일을 업로드해 주세요.")

    # primary 문서가 속한 test_type 의 기출 스타일 팩 로드 ('기출' 선택 시에만 적용)
    doc = db.get(models.Document, primary_doc_id)
    test_type = doc.test_type if doc else None
    pack_name, pack_text = ("default", "")
    pastexam = _has_pastexam_style(style)
    if pastexam:
        pack_name, pack_text = _load_style_pack(test_type)

    style_pack_block = ""
    if pastexam and pack_text:
        style_pack_block = (
            "\n\n=== STYLE_PACK: " + pack_name + " ===\n"
            + pack_text
            + "\n=== STYLE_PACK END ===\n\n"
            "위 STYLE_PACK 의 형식·어투·지문 블록 규칙을 반드시 따르고, "
            "지문이 필요한 영역은 SOURCE 에서 발췌·요약한 passage 와 group_id 를 부여하세요."
        )

    excluded = _excluded_stems_multi(db, user_id, doc_ids)
    excluded_block = ""
    if excluded:
        bullets = "\n".join(f"- {s}" for s in excluded)
        excluded_block = (
            "\nEXCLUDED_QUESTIONS (사용자가 제외 처리 — 동일하거나 매우 유사한 문제는 출제하지 마세요):\n"
            f"{bullets}\n"
        )

    user_msg = (
        f"TEST_TYPE: {test_type or '-'}\n"
        f"N: {n}\n"
        f"TYPE_MIX: mcq={type_mix.get('mcq', 0)}, short={type_mix.get('short', 0)}\n"
        f"STYLE: {style}\n"
        f"EXTRA: {extra_instructions or '-'}\n"
        f"TOPIC_HINT: {topic_hint or '-'}\n"
        f"{excluded_block}"
        f"{style_pack_block}"
        f"\nSOURCE:\n---\n{source}\n---\n\n"
        f"위 SOURCE 만 근거로 정확히 {n}문항을 출제해 JSON 배열로만 반환하세요."
        f"{' EXCLUDED_QUESTIONS 와는 주제·표현이 충분히 다른 문항만 만들 것.' if excluded else ''}"
    )

    difficulty = _detect_difficulty(extra_instructions)
    high_difficulty = difficulty in ("상", "최상")
    target_sentences = 70 if difficulty == "최상" else 50 if difficulty == "상" else 0

    # 기출/고난도 스타일은 지문이 길어질 수 있어 토큰 더 여유롭게
    if difficulty == "최상":
        max_tokens = 12000
    elif difficulty == "상":
        max_tokens = 9000
    elif pastexam:
        max_tokens = 6000
    else:
        max_tokens = 4096

    text = call_claude(SYSTEM_PROMPT, user_msg, max_tokens=max_tokens, temperature=0.5)
    parsed = parse_json_block(text)
    if not isinstance(parsed, list):
        raise ValueError(f"LLM 응답에서 JSON 배열을 파싱하지 못했습니다. 원문: {text[:300]}")

    items = _validate(parsed, type_mix)[:n]

    # 상/최상 난이도일 때 passage 길이 미달 OR 시나리오 키워드 부재면 1회 재시도
    if high_difficulty and items:
        too_short = [
            it for it in items
            if it.get("passage") and _count_sentences(it["passage"]) < target_sentences
        ]
        no_scenario = [
            it for it in items
            if it.get("passage") and not _has_scenario(it["passage"])
        ]
        passage_count = sum(1 for it in items if it.get("passage"))
        need_retry = (
            passage_count == 0
            or len(too_short) >= max(1, passage_count // 2)
            or len(no_scenario) >= max(1, passage_count // 2)
        )
        if need_retry:
            reasons: list[str] = []
            if passage_count == 0 or too_short:
                reasons.append(
                    f"passage 가 {target_sentences}문장 미만이거나 누락"
                )
            if no_scenario:
                reasons.append(
                    "passage 가 가상 사건·상황·사례·시나리오 형태가 아님 (단순 정의 나열 금지)"
                )
            retry_msg = (
                user_msg
                + "\n\nRETRY: 이전 응답에 다음 문제가 있습니다 — "
                + "; ".join(reasons)
                + f". 난이도 '{difficulty}' 규칙을 반드시 지켜 다시 생성하세요.\n"
                + f"- passage 최소 {target_sentences}문장 이상, 여러 단락으로 구성.\n"
                "- 강의노트 핵심 개념을 그대로 나열하지 말고, **가상의 회사명/팀명/담당자/사건/시점**을 "
                "설정하고 그 안에서 개념이 적용된 사례를 서술할 것.\n"
                "- 그 사례 안에서 부적합 항목/누락/오류를 묻는 형식으로 stem 을 작성할 것.\n"
                "- 행간 추론·조건 분기·모순 탐지를 요구하는 문항을 묶어 주세요."
            )
            text2 = call_claude(SYSTEM_PROMPT, retry_msg, max_tokens=max_tokens, temperature=0.4)
            parsed2 = parse_json_block(text2)
            if isinstance(parsed2, list):
                items2 = _validate(parsed2, type_mix)[:n]
                if items2:
                    items = items2

    if not items:
        raise ValueError("LLM 이 유효한 문항을 만들지 못했습니다. 다시 시도해 주세요.")

    saved: list[models.Question] = []
    for it in items:
        passage = it.get("passage")
        if passage is not None:
            passage = str(passage).strip() or None
        group_id = it.get("group_id")
        if group_id is not None:
            group_id = str(group_id).strip() or None

        # LLM 이 source_doc_id 를 명시했으면 그 문서, 아니면 primary
        src = it.get("source_doc_id") or it.get("document_id")
        try:
            src = int(src) if src is not None else None
        except (TypeError, ValueError):
            src = None
        question_doc_id = src if src in valid_ids else primary_doc_id

        q = models.Question(
            owner_id=user_id,
            document_id=question_doc_id,
            type=it["type"],
            style=style,
            stem=it["stem"].strip(),
            choices=it.get("choices"),
            answer=it["answer"],
            rationale=(it.get("rationale") or "").strip() or None,
            refs=None,
            topic_tag=(it.get("topic_tag") or "").strip() or None,
            passage=passage,
            group_id=group_id,
        )
        db.add(q)
        saved.append(q)
    db.flush()
    return saved
