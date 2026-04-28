"""시험 범위 vs 강의노트 비교 분석.

- exam_scope/<test_type>.md 가 있으면 그것을 시험 범위로 사용
- 없으면 LLM 에 한 번 질의해 시험 범위 토픽 리스트를 만들고 DB(exam_scopes) 에 캐시
- 강의노트 토픽도 LLM 으로 한 번 추출해 documents.topics 에 캐시
- 시험 범위 토픽 ↔ 강의노트 토픽 매핑은 토픽명 정규화 + 부분 매칭으로 빠르게 계산
"""
from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy.orm import Session

from .. import models
from .llm import LLMUnavailableError, call_claude, parse_json_block


SCOPE_DIR = Path(__file__).resolve().parent.parent / "exam_scope"


# --------------------------------------------------------------------------- #
# 시험 범위 (file → DB cache → LLM fetch)
# --------------------------------------------------------------------------- #

def _load_scope_file(test_type: str) -> str | None:
    target = (test_type or "").strip()
    if not target:
        return None
    candidates = list(SCOPE_DIR.glob("*.md"))
    for p in candidates:
        if p.stem == target:
            return p.read_text(encoding="utf-8")
    lower = target.lower()
    for p in candidates:
        if p.stem.lower() == lower:
            return p.read_text(encoding="utf-8")
    for p in candidates:
        if p.stem == "default":
            continue
        if p.stem in target or target in p.stem:
            return p.read_text(encoding="utf-8")
    return None


def _scope_topics_from_md(md: str) -> list[dict]:
    topics: list[dict] = []
    section = ""
    for line in md.splitlines():
        line = line.rstrip()
        if line.startswith("## "):
            section = line[3:].strip()
        elif line.startswith("### "):
            section = line[4:].strip()
        elif line.startswith("- "):
            name = line[2:].strip()
            if ":" in name:
                name = name.split(":", 1)[0].strip()
            if name and len(name) <= 80:
                topics.append({"name": name, "section": section})
    seen = set()
    out = []
    for t in topics:
        if t["name"] in seen:
            continue
        seen.add(t["name"])
        out.append(t)
    return out


_LLM_SYSTEM_SCOPE = """당신은 한국 자격시험 출제기관 자료에 정통한 분석가입니다.
주어진 시험명에 대해 표준 시험 범위(영역·세부 토픽) 를 JSON 으로만 반환합니다.

규칙:
1. 한국에서 통용되는 공식 명칭 기준. 자격시험이 아니거나 정보가 없으면 빈 배열 [].
2. 각 토픽은 한국어 명사구 (10~30자).
3. 30~80개 토픽. 너무 세분화하지 말 것.
4. 응답은 JSON 배열만:
   [{"name":"...", "section":"...상위영역...", "ref_url":"공식 안내 URL or null"}]
"""


def fetch_scope_via_llm(test_type: str) -> list[dict] | None:
    try:
        text = call_claude(
            _LLM_SYSTEM_SCOPE,
            f"시험명: {test_type}\n\n위 시험의 표준 시험 범위를 JSON 배열로만 반환하세요.",
            max_tokens=2000, temperature=0.0,
        )
    except LLMUnavailableError:
        return None
    except Exception:
        return None
    obj = parse_json_block(text)
    if not isinstance(obj, list):
        return None
    out = []
    for it in obj[:120]:
        if not isinstance(it, dict):
            continue
        name = (it.get("name") or "").strip()
        if not name:
            continue
        out.append({
            "name": name,
            "section": (it.get("section") or "").strip(),
            "ref_url": it.get("ref_url") or None,
        })
    return out or None


def get_scope_topics(db: Session, test_type: str) -> tuple[list[dict], str]:
    """반환: (topics, source) — source: 'file' | 'cache' | 'llm' | 'empty'"""
    md = _load_scope_file(test_type)
    if md:
        topics = _scope_topics_from_md(md)
        if topics:
            return topics, "file"

    cached = db.get(models.ExamScope, test_type)
    if cached and cached.topics:
        return list(cached.topics), "cache"

    fetched = fetch_scope_via_llm(test_type)
    if fetched:
        if cached:
            cached.source = "llm"
            cached.topics = fetched
        else:
            db.add(models.ExamScope(test_type=test_type, source="llm", topics=fetched))
        db.commit()
        return fetched, "llm"

    return [], "empty"


# --------------------------------------------------------------------------- #
# 강의노트 토픽 추출
# --------------------------------------------------------------------------- #

_LLM_SYSTEM_NOTE = """당신은 강의노트 분석가입니다. 주어진 본문(SOURCE)에서 핵심 학습 토픽을 한국어 명사구로 추출합니다.

규칙:
1. 10~40개. 너무 세분화하지 말 것.
2. 각 토픽은 10~30자.
3. 응답은 JSON 배열만:
   ["토픽1", "토픽2", ...]
"""


def extract_note_topics(db: Session, document_id: int) -> list[str] | None:
    doc = db.get(models.Document, document_id)
    if doc is None:
        return None
    if isinstance(doc.topics, list) and doc.topics:
        return list(doc.topics)

    rows = (
        db.query(models.Chunk.content)
        .filter(models.Chunk.document_id == document_id)
        .order_by(models.Chunk.seq.asc())
        .limit(60)
        .all()
    )
    body = "\n\n".join(r[0] for r in rows if r and r[0])[:12000]
    if not body.strip():
        return None

    has_table = "[TABLE]" in body
    has_image = "[IMAGE_PAGE:" in body
    hint_lines: list[str] = []
    if has_table:
        hint_lines.append(
            "- SOURCE 안의 `[TABLE]` 블록은 표입니다. 표의 캡션·컬럼명·핵심 항목명을 토픽으로 우선 추출하세요."
        )
    if has_image:
        hint_lines.append(
            "- `[IMAGE_PAGE: n]` 마커는 이미지 위주 페이지로 텍스트 추출이 불완전합니다. 해당 페이지의 단편 키워드만으로 토픽을 단정하지 마세요."
        )
    hint_block = ("\n\n참고:\n" + "\n".join(hint_lines)) if hint_lines else ""

    try:
        text = call_claude(
            _LLM_SYSTEM_NOTE,
            f"SOURCE:\n---\n{body}\n---{hint_block}\n\n핵심 토픽 JSON 배열만 반환하세요.",
            max_tokens=900, temperature=0.0,
        )
    except LLMUnavailableError:
        return None
    except Exception:
        return None
    obj = parse_json_block(text)
    topics: list[str] = []
    if isinstance(obj, list):
        for x in obj[:60]:
            if isinstance(x, str) and x.strip():
                topics.append(x.strip())
    if not topics:
        return None
    doc.topics = topics
    db.commit()
    return topics


# --------------------------------------------------------------------------- #
# 비교 (정규화 + 부분 매칭)
# --------------------------------------------------------------------------- #

_NORM = re.compile(r"[\s/().,\-]+")


def _norm(s: str) -> str:
    return _NORM.sub("", (s or "").lower())


def match_topics(scope_topics: list[dict], note_topics: list[str]) -> dict:
    note_norm = [(_norm(t), t) for t in note_topics]
    covered_scope: list[dict] = []
    missing_scope: list[dict] = []
    matched_note_idx: set[int] = set()
    for st in scope_topics:
        sn = _norm(st["name"])
        hit = None
        for i, (nn, _orig) in enumerate(note_norm):
            if not nn or not sn:
                continue
            if nn == sn or sn in nn or nn in sn:
                hit = i
                break
        if hit is not None:
            matched_note_idx.add(hit)
            covered_scope.append(st)
        else:
            missing_scope.append(st)
    extra = [t for i, t in enumerate(note_topics) if i not in matched_note_idx]
    return {"covered": covered_scope, "missing": missing_scope, "extra": extra}
