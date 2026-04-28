from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import models, schemas
from ..db import get_db
from ..deps import ensure_user
from ..services import explain_summarizer, grader, mastery, quiz_gen, retriever, scope_analyzer, verifier
from ..services.llm import LLMUnavailableError


router = APIRouter(prefix="/api/quiz", tags=["quiz"])


def _to_question_out(q: models.Question) -> schemas.QuestionOut:
    return schemas.QuestionOut(
        id=q.id,
        type=q.type,
        style=q.style,
        stem=q.stem,
        choices=q.choices,
        topic_tag=q.topic_tag,
        session_id=q.session_id,
        excluded=bool(q.excluded),
        passage=q.passage,
        group_id=q.group_id,
    )


def _session_style(sess: models.QuizSession) -> str:
    """quiz_sessions.options JSONB 에서 style 또는 styles 를 추출. 비어있으면 ''."""
    opts = getattr(sess, "options", None)
    if isinstance(opts, dict):
        v = opts.get("styles") or opts.get("style")
        if isinstance(v, list):
            return ", ".join(str(x) for x in v if x)
        if v:
            return str(v)
    return ""


def _build_session_title(doc: models.Document, n: int) -> str:
    return _build_session_title_label(doc.filename, n)


def _build_session_title_label(label: str, n: int) -> str:
    when = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    return f"{when} · {label} · {n}문항"


@router.post("/generate", response_model=schemas.GenerateResponse)
def generate(req: schemas.GenerateRequest, db: Session = Depends(get_db)):
    try:
        ensure_user(db, req.user_id)
    except ValueError as e:
        raise HTTPException(400, str(e))

    doc_ids = req.resolved_document_ids()
    if not doc_ids:
        raise HTTPException(400, "출제 범위에 포함할 강의노트를 1개 이상 선택하세요.")

    docs = (
        db.query(models.Document)
        .filter(models.Document.id.in_(doc_ids), models.Document.owner_id == req.user_id)
        .all()
    )
    if not docs:
        raise HTTPException(404, "선택한 강의노트를 찾을 수 없습니다.")
    docs_by_id = {d.id: d for d in docs}
    # 클라이언트가 보낸 순서를 유지 (잘못된 id 는 제거)
    doc_ids = [i for i in doc_ids if i in docs_by_id]
    primary_doc = docs_by_id[doc_ids[0]]

    n = max(1, min(20, int(req.n)))
    type_mix = {"mcq": req.type_mix.mcq, "short": req.type_mix.short}

    title_label = (
        f"{primary_doc.filename} 외 {len(doc_ids) - 1}건"
        if len(doc_ids) > 1 else primary_doc.filename
    )

    sess = models.QuizSession(
        owner_id=req.user_id,
        document_id=primary_doc.id,
        document_ids=doc_ids,
        test_type=primary_doc.test_type,
        title=_build_session_title_label(title_label, n),
        n_questions=n,
        options={
            "style": req.style,
            "type_mix": type_mix,
            "extra_instructions": req.extra_instructions or "",
        },
    )
    db.add(sess)
    db.flush()

    try:
        items = quiz_gen.generate_questions(
            db,
            user_id=req.user_id,
            document_ids=doc_ids,
            n=n,
            type_mix=type_mix,
            style=req.style,
            extra_instructions=req.extra_instructions,
        )
    except LLMUnavailableError as e:
        db.rollback()
        raise HTTPException(503, str(e))
    except ValueError as e:
        db.rollback()
        raise HTTPException(422, str(e))

    for q in items:
        q.session_id = sess.id
    sess.n_questions = len(items)

    db.commit()
    return schemas.GenerateResponse(
        session_id=sess.id,
        questions=[_to_question_out(q) for q in items],
    )


@router.post("/answer", response_model=schemas.SubmittedResponse)
def answer(req: schemas.AnswerRequest, db: Session = Depends(get_db)):
    """답안을 즉시 채점·저장하지만 결과·해설은 응답에 포함하지 않는다.

    프론트는 '제출됨' 상태만 표시. 사용자가 명시적으로 해설을 요청할 때
    `/api/quiz/explain/{question_id}` 로 노출.
    """
    try:
        ensure_user(db, req.user_id)
    except ValueError as e:
        raise HTTPException(400, str(e))

    q = db.get(models.Question, req.question_id)
    if q is None:
        raise HTTPException(404, "문제를 찾을 수 없습니다.")
    if q.excluded:
        raise HTTPException(
            409,
            "제외 처리된 문제는 채점되지 않습니다. 제외를 해제한 뒤 다시 제출하세요.",
        )

    refs = retriever.find_references(
        db,
        q.document_id,
        q.stem,
        str(q.answer or ""),
        limit=3,
    )
    snippets = [r["snippet"] for r in refs]

    if q.type == "mcq":
        correct, feedback = grader.grade_mcq(q, req.user_answer)
    else:
        try:
            correct, feedback, _rationale = grader.grade_short(q, req.user_answer, snippets)
        except LLMUnavailableError as e:
            raise HTTPException(503, str(e))

    attempt = models.Attempt(
        owner_id=req.user_id,
        question_id=q.id,
        user_answer=req.user_answer,
        correct=correct,
        feedback=feedback,
    )
    db.add(attempt)
    db.flush()

    mastery.update(db, user_id=req.user_id, topic_tag=q.topic_tag, correct=correct)

    if q.session_id is not None:
        sess = db.get(models.QuizSession, q.session_id)
        if sess and sess.finished_at is None:
            attempted = (
                db.query(func.count(func.distinct(models.Attempt.question_id)))
                .join(models.Question, models.Question.id == models.Attempt.question_id)
                .filter(models.Question.session_id == sess.id, models.Attempt.owner_id == sess.owner_id)
                .scalar()
                or 0
            )
            if attempted >= (sess.n_questions or 0) and (sess.n_questions or 0) > 0:
                sess.finished_at = func.now()

    db.commit()
    return schemas.SubmittedResponse(
        submitted=True,
        question_id=q.id,
        attempt_id=int(attempt.id),
    )


@router.get("/question/{question_id}", response_model=schemas.QuestionReplay)
def question_replay(
    question_id: int,
    user_id: str = Query(...),
    db: Session = Depends(get_db),
):
    """동일 문제를 다시 풀기 위한 페이로드.

    권한: 문제의 session.owner_id 와 user_id 가 일치해야 함 (없으면 question.owner_id 폴백).
    응답에는 stem/choices/qtype/passage 만 포함하며 정답·근거·해설은 절대 노출하지 않는다.
    클라이언트는 이 응답으로 카드를 띄우고 `/api/quiz/answer` 로 제출.
    """
    q = db.get(models.Question, question_id)
    if q is None:
        raise HTTPException(404, "문제를 찾을 수 없습니다.")

    owner = q.owner_id
    if q.session_id is not None:
        sess = db.get(models.QuizSession, q.session_id)
        if sess is not None:
            owner = sess.owner_id
    if owner != user_id:
        raise HTTPException(404, "문제를 찾을 수 없습니다.")

    return schemas.QuestionReplay(
        id=q.id,
        type=q.type,
        style=q.style,
        stem=q.stem,
        choices=q.choices,
        topic_tag=q.topic_tag,
        session_id=q.session_id,
        excluded=bool(q.excluded),
        passage=q.passage,
        group_id=q.group_id,
    )


@router.post("/replay/{question_id}", response_model=schemas.QuestionReplay)
def question_replay_post(
    question_id: int,
    user_id: str = Query(...),
    db: Session = Depends(get_db),
):
    """`GET /question/{id}` 의 alias (POST 선호 클라이언트용)."""
    return question_replay(question_id, user_id=user_id, db=db)


@router.get("/explain/{question_id}", response_model=schemas.ExplainResponse)
def explain(question_id: int, user_id: str = Query(...), db: Session = Depends(get_db)):
    """사용자가 명시 요청 시 채점 결과·정답·근거·레퍼런스 노출.

    LLM 으로 해설을 정확·요약하고, 원문 청크에서 인용문을 1~3개 추출해 캐시한다.
    이후 같은 문제에 대한 재요청은 캐시된 결과를 즉시 반환.
    """
    q = db.get(models.Question, question_id)
    if q is None or q.owner_id != user_id:
        raise HTTPException(404, "문제를 찾을 수 없습니다.")

    last_attempt = (
        db.query(models.Attempt)
        .filter(models.Attempt.owner_id == user_id, models.Attempt.question_id == q.id)
        .order_by(models.Attempt.attempted_at.desc())
        .first()
    )
    if last_attempt is None:
        raise HTTPException(409, "아직 답안을 제출하지 않았습니다. 제출 후 해설을 요청하세요.")

    # 1) 원시 청크 검색 (pg_trgm)
    raw_refs = retriever.find_references(
        db, q.document_id, q.stem, str(q.answer or ""), limit=3
    )

    rationale = q.rationale or last_attempt.feedback or ""
    references: list[dict] = list(raw_refs)  # 기본값: 원문 청크

    # 2) 캐시 확인 (q.refs 에 {"summary":..., "quotes":[...]} 가 있으면 재사용)
    cached = q.refs if isinstance(q.refs, dict) else None

    if cached and cached.get("rationale"):
        rationale = cached["rationale"] or rationale
        quotes = cached.get("quotes") or []
        references = _quotes_to_references(quotes, raw_refs)
    else:
        # 3) LLM 요약 호출 시도 (실패 시 원문 그대로)
        summary = explain_summarizer.summarize_explanation(
            stem=q.stem,
            qtype=q.type,
            choices=q.choices,
            correct_answer=q.answer,
            raw_refs=raw_refs,
        )
        if summary is not None:
            rationale = summary.get("rationale") or rationale
            quotes = summary.get("quotes") or []
            references = _quotes_to_references(quotes, raw_refs)
            # 캐시 저장
            q.refs = {"rationale": rationale, "quotes": quotes}
            db.commit()

    m = (
        db.query(models.Mastery)
        .filter(models.Mastery.owner_id == user_id, models.Mastery.topic_tag == q.topic_tag)
        .one_or_none()
    )

    return schemas.ExplainResponse(
        question_id=q.id,
        correct=bool(last_attempt.correct),
        correct_answer=q.answer,
        rationale=rationale,
        references=[schemas.Reference(**r) for r in references],
        mastery_level=int(m.level) if m else 0,
        topic_tag=q.topic_tag,
        user_answer=last_attempt.user_answer,
    )


def _quotes_to_references(quotes: list, raw_refs: list[dict]) -> list[dict]:
    """LLM 이 추출한 인용문을 Reference 형식으로 변환.

    - quote 자체를 snippet 으로 사용
    - chunk_id 가 raw_refs 에 있는 값과 매칭되면 그 document_id 를 사용
    - 빈 quotes 면 raw_refs 그대로 (요약 실패 케이스 폴백)
    """
    if not quotes:
        return list(raw_refs)
    by_id = {r["chunk_id"]: r for r in raw_refs if "chunk_id" in r}
    out: list[dict] = []
    for q in quotes:
        cid = q.get("chunk_id")
        snippet = q.get("quote") or ""
        if not snippet:
            continue
        ref = by_id.get(cid) if cid is not None else None
        if ref:
            out.append({
                "document_id": ref["document_id"],
                "chunk_id": ref["chunk_id"],
                "snippet": snippet,
            })
        else:
            # 매칭 실패해도 quote 만 유지 (UI 가 doc#?·chunk#? 로 표시)
            out.append({
                "document_id": raw_refs[0]["document_id"] if raw_refs else 0,
                "chunk_id": int(cid) if isinstance(cid, int) else 0,
                "snippet": snippet,
            })
    return out or list(raw_refs)


@router.post("/similar", response_model=schemas.GenerateResponse)
def similar(req: schemas.SimilarRequest, db: Session = Depends(get_db)):
    try:
        ensure_user(db, req.user_id)
    except ValueError as e:
        raise HTTPException(400, str(e))

    base = db.get(models.Question, req.question_id)
    if base is None:
        raise HTTPException(404, "원본 문제를 찾을 수 없습니다.")

    type_mix = {"mcq": 1, "short": 0} if base.type == "mcq" else {"mcq": 0, "short": 1}

    try:
        items = quiz_gen.generate_questions(
            db,
            user_id=req.user_id,
            document_id=base.document_id,
            n=1,
            type_mix=type_mix,
            style=base.style or "기출",
            extra_instructions=f"기존 문제와 같은 주제·난이도로 변형 출제. 기존 stem: {base.stem}",
            topic_hint=base.topic_tag,
        )
    except LLMUnavailableError as e:
        raise HTTPException(503, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))

    if base.session_id is not None:
        for q in items:
            q.session_id = base.session_id

    db.commit()
    return schemas.GenerateResponse(
        session_id=base.session_id,
        questions=[_to_question_out(q) for q in items],
    )


# ---------- 풀이 이력 ----------

@router.get("/test-types")
def test_types(user_id: str = Query(...), db: Session = Depends(get_db)):
    """사용자가 보유한 테스트 타입 목록 + 각 타입의 문서 수."""
    rows = (
        db.query(
            models.Document.test_type.label("tt"),
            func.count(models.Document.id).label("doc_count"),
            func.max(models.Document.uploaded_at).label("last_at"),
        )
        .filter(models.Document.owner_id == user_id)
        .group_by(models.Document.test_type)
        .order_by(func.max(models.Document.uploaded_at).desc())
        .all()
    )
    return [
        {
            "test_type": r.tt,
            "document_count": int(r.doc_count or 0),
            "last_uploaded_at": r.last_at.isoformat() if r.last_at else None,
        }
        for r in rows
        if r.tt
    ]


@router.get("/tree")
def tree(user_id: str = Query(...), db: Session = Depends(get_db)):
    """좌측 사이드바 트리: test_type → document → session(통계 포함)."""
    docs = (
        db.query(models.Document)
        .filter(models.Document.owner_id == user_id)
        .order_by(models.Document.test_type.asc(), models.Document.uploaded_at.desc())
        .all()
    )
    if not docs:
        return []

    sessions_by_doc: dict[int, list[models.QuizSession]] = {}
    sids = []
    for s in (
        db.query(models.QuizSession)
        .filter(models.QuizSession.owner_id == user_id)
        .order_by(models.QuizSession.created_at.desc())
        .all()
    ):
        sessions_by_doc.setdefault(s.document_id, []).append(s)
        sids.append(s.id)

    # 회차별 시도/정답 집계 (가장 최근 시도만 카운트)
    stats: dict[int, dict[int, bool]] = {sid: {} for sid in sids}
    when: dict[int, dict[int, datetime]] = {sid: {} for sid in sids}
    if sids:
        attempts = (
            db.query(
                models.Question.session_id,
                models.Attempt.question_id,
                models.Attempt.correct,
                models.Attempt.attempted_at,
            )
            .join(models.Question, models.Question.id == models.Attempt.question_id)
            .filter(
                models.Question.session_id.in_(sids),
                models.Attempt.owner_id == user_id,
            )
            .all()
        )
        for sid, qid, correct, at in attempts:
            if qid not in when[sid] or at > when[sid][qid]:
                when[sid][qid] = at
                stats[sid][qid] = bool(correct)

    by_type: dict[str, dict] = {}
    for d in docs:
        tt = d.test_type or "기본"
        node = by_type.setdefault(tt, {"test_type": tt, "documents": []})
        sess_payload = []
        for s in sessions_by_doc.get(d.id, []):
            attempted = len(stats[s.id])
            correct = sum(1 for v in stats[s.id].values() if v)
            sess_payload.append(
                {
                    "id": s.id,
                    "title": s.title,
                    "n_questions": int(s.n_questions or 0),
                    "n_attempted": attempted,
                    "n_correct": correct,
                    "created_at": s.created_at.isoformat() if s.created_at else None,
                    "finished_at": s.finished_at.isoformat() if s.finished_at else None,
                }
            )
        node["documents"].append(
            {
                "id": d.id,
                "filename": d.filename,
                "uploaded_at": d.uploaded_at.isoformat() if d.uploaded_at else None,
                "sessions": sess_payload,
            }
        )

    return list(by_type.values())


@router.get("/scope-dashboard")
def scope_dashboard(
    user_id: str = Query(...),
    test_type: str = Query(...),
    refresh: bool = Query(False),
    db: Session = Depends(get_db),
):
    """시험 범위 vs 강의노트 비교 + 출제·오답 빈도 대시보드.

    - covered: 시험 범위 안에서 강의노트가 다루는 토픽
    - missing: 시험 범위 중 노트에 없는 영역 (= 학습 갭)
    - extra:   노트에 있지만 시험 범위 밖의 영역
    - per_topic_stats: questions.topic_tag 기반 출제·오답 빈도
    - refresh=true 면 documents.topics 캐시를 무시하고 재추출
    """
    docs = (
        db.query(models.Document)
        .filter(models.Document.owner_id == user_id, models.Document.test_type == test_type)
        .all()
    )

    # 1) 시험 범위
    scope_topics, scope_source = scope_analyzer.get_scope_topics(db, test_type)

    # 2) 강의노트 토픽 (모든 문서 합집합)
    if refresh:
        for d in docs:
            d.topics = None
        db.commit()
    note_topics_all: list[str] = []
    seen = set()
    for d in docs:
        topics = scope_analyzer.extract_note_topics(db, d.id) or list(d.topics or [])
        for t in topics:
            if t not in seen:
                seen.add(t)
                note_topics_all.append(t)

    # 3) 매칭
    match = scope_analyzer.match_topics(scope_topics, note_topics_all)

    # 4) 토픽별 출제·오답 빈도 (questions.topic_tag 기준; 시험 범위/노트 토픽 모두 매핑)
    questions_q = (
        db.query(models.Question.topic_tag, models.Question.id)
        .join(models.QuizSession, models.QuizSession.id == models.Question.session_id)
        .filter(
            models.QuizSession.owner_id == user_id,
            models.QuizSession.test_type == test_type,
            models.Question.topic_tag.isnot(None),
        )
        .all()
    )
    per_topic: dict[str, dict] = {}
    for tag, qid in questions_q:
        bucket = per_topic.setdefault(tag, {"asked": 0, "correct": 0, "wrong": 0})
        bucket["asked"] += 1

    # attempts 집계 (마지막 시도 기준)
    attempts_q = (
        db.query(
            models.Question.topic_tag, models.Attempt.question_id,
            models.Attempt.correct, models.Attempt.attempted_at,
        )
        .join(models.Question, models.Question.id == models.Attempt.question_id)
        .join(models.QuizSession, models.QuizSession.id == models.Question.session_id)
        .filter(
            models.QuizSession.owner_id == user_id,
            models.QuizSession.test_type == test_type,
            models.Question.topic_tag.isnot(None),
        )
        .all()
    )
    latest_at: dict[int, datetime] = {}
    latest_correct: dict[int, bool] = {}
    latest_tag: dict[int, str] = {}
    for tag, qid, correct, at in attempts_q:
        if qid not in latest_at or at > latest_at[qid]:
            latest_at[qid] = at
            latest_correct[qid] = bool(correct)
            latest_tag[qid] = tag
    for qid, ok in latest_correct.items():
        tag = latest_tag.get(qid)
        if not tag:
            continue
        bucket = per_topic.setdefault(tag, {"asked": 0, "correct": 0, "wrong": 0})
        if ok:
            bucket["correct"] += 1
        else:
            bucket["wrong"] += 1

    # 출제 빈도·오답률을 시험 범위 토픽에 매칭 (topic_tag → scope name fuzzy match)
    def _attach_stats(name: str) -> dict:
        norm = scope_analyzer._norm(name)
        for tag, st in per_topic.items():
            tn = scope_analyzer._norm(tag)
            if tn == norm or norm in tn or tn in norm:
                wrong_rate = (st["wrong"] / max(1, st["correct"] + st["wrong"]))
                return {
                    "asked": st["asked"], "correct": st["correct"], "wrong": st["wrong"],
                    "wrong_rate": round(wrong_rate, 3),
                }
        return {"asked": 0, "correct": 0, "wrong": 0, "wrong_rate": None}

    covered = [{**t, "stats": _attach_stats(t["name"])} for t in match["covered"]]
    missing = [{**t, "stats": _attach_stats(t["name"])} for t in match["missing"]]
    extra   = [{"name": t, "stats": _attach_stats(t)} for t in match["extra"]]

    # 합계
    total_asked = sum(b["asked"] for b in per_topic.values())
    total_wrong = sum(b["wrong"] for b in per_topic.values())
    total_attempted = sum(b["correct"] + b["wrong"] for b in per_topic.values())

    return {
        "test_type": test_type,
        "scope_source": scope_source,                # 'file' | 'cache' | 'llm' | 'empty'
        "scope_total": len(scope_topics),
        "note_total": len(note_topics_all),
        "coverage_pct": round(100 * len(covered) / max(1, len(scope_topics)), 1) if scope_topics else None,
        "covered": covered,
        "missing": missing,
        "extra": extra,
        "totals": {
            "asked": total_asked,
            "attempted": total_attempted,
            "wrong": total_wrong,
            "wrong_rate": round(total_wrong / max(1, total_attempted), 3) if total_attempted else None,
        },
        "documents": [{"id": d.id, "filename": d.filename, "topics": list(d.topics or [])} for d in docs],
    }


@router.get("/dashboard")
def dashboard(user_id: str = Query(...), db: Session = Depends(get_db)):
    """우측 상단 대시보드: test_type 별 종합 통계."""
    types_rows = (
        db.query(models.Document.test_type)
        .filter(models.Document.owner_id == user_id)
        .group_by(models.Document.test_type)
        .all()
    )
    types = [r.test_type for r in types_rows if r.test_type]
    if not types:
        return []

    out = []
    for tt in types:
        n_docs = (
            db.query(func.count(models.Document.id))
            .filter(
                models.Document.owner_id == user_id,
                models.Document.test_type == tt,
            )
            .scalar()
            or 0
        )
        n_sess = (
            db.query(func.count(models.QuizSession.id))
            .filter(
                models.QuizSession.owner_id == user_id,
                models.QuizSession.test_type == tt,
            )
            .scalar()
            or 0
        )
        n_q = (
            db.query(func.count(models.Question.id))
            .join(
                models.QuizSession,
                models.QuizSession.id == models.Question.session_id,
            )
            .filter(
                models.QuizSession.owner_id == user_id,
                models.QuizSession.test_type == tt,
            )
            .scalar()
            or 0
        )

        # 최신 시도만 모아 정답률
        attempts = (
            db.query(
                models.Attempt.question_id,
                models.Attempt.correct,
                models.Attempt.attempted_at,
            )
            .join(models.Question, models.Question.id == models.Attempt.question_id)
            .join(
                models.QuizSession,
                models.QuizSession.id == models.Question.session_id,
            )
            .filter(
                models.QuizSession.owner_id == user_id,
                models.QuizSession.test_type == tt,
            )
            .all()
        )
        latest: dict[int, bool] = {}
        latest_at: dict[int, datetime] = {}
        last_activity = None
        for qid, correct, at in attempts:
            if qid not in latest_at or at > latest_at[qid]:
                latest_at[qid] = at
                latest[qid] = bool(correct)
            if last_activity is None or at > last_activity:
                last_activity = at
        n_attempted = len(latest)
        n_correct = sum(1 for v in latest.values() if v)
        rate = (n_correct / n_attempted) if n_attempted else None

        # topic_tag 기준 약점/체득
        topic_rows = (
            db.query(
                models.Question.topic_tag,
                func.count().label("c"),
            )
            .join(
                models.QuizSession,
                models.QuizSession.id == models.Question.session_id,
            )
            .filter(
                models.QuizSession.owner_id == user_id,
                models.QuizSession.test_type == tt,
                models.Question.topic_tag.isnot(None),
            )
            .group_by(models.Question.topic_tag)
            .all()
        )
        topic_tags = [r.topic_tag for r in topic_rows]
        weak_topics = []
        if topic_tags:
            for m in (
                db.query(models.Mastery)
                .filter(
                    models.Mastery.owner_id == user_id,
                    models.Mastery.topic_tag.in_(topic_tags),
                )
                .all()
            ):
                if (m.level or 0) < 3 or (m.wrong_count or 0) > 0:
                    weak_topics.append(
                        {
                            "topic_tag": m.topic_tag,
                            "level": int(m.level or 0),
                            "wrong_count": int(m.wrong_count or 0),
                        }
                    )

        out.append(
            {
                "test_type": tt,
                "documents": int(n_docs),
                "sessions": int(n_sess),
                "questions": int(n_q),
                "attempted": int(n_attempted),
                "correct": int(n_correct),
                "correct_rate": rate,
                "weak_topics": sorted(weak_topics, key=lambda x: (x["level"], -x["wrong_count"]))[:5],
                "last_activity_at": last_activity.isoformat() if last_activity else None,
            }
        )
    out.sort(key=lambda x: (x["last_activity_at"] or ""), reverse=True)
    return out


@router.get("/sessions", response_model=list[schemas.SessionListItem])
def sessions_list(user_id: str = Query(...), db: Session = Depends(get_db)):
    """사용자의 회차(quiz_session) 목록.

    각 회차에 대해 문항 수, 시도 수, 정답 수, 문서 파일명을 조회.
    """
    rows = (
        db.query(models.QuizSession, models.Document.filename)
        .outerjoin(models.Document, models.Document.id == models.QuizSession.document_id)
        .filter(models.QuizSession.owner_id == user_id)
        .order_by(models.QuizSession.created_at.desc())
        .all()
    )

    out: list[schemas.SessionListItem] = []
    for sess, fname in rows:
        # 시도/정답 집계 — 같은 문제를 여러 번 제출해도 최신 1회로만 카운트
        attempts = (
            db.query(
                models.Attempt.question_id,
                models.Attempt.correct,
                models.Attempt.attempted_at,
            )
            .join(models.Question, models.Question.id == models.Attempt.question_id)
            .filter(
                models.Question.session_id == sess.id,
                models.Attempt.owner_id == sess.owner_id,
            )
            .all()
        )
        latest: dict[int, bool] = {}
        latest_when: dict[int, datetime] = {}
        for qid, correct, when in attempts:
            if qid not in latest_when or when > latest_when[qid]:
                latest_when[qid] = when
                latest[qid] = bool(correct)

        n_attempted = len(latest)
        n_correct = sum(1 for v in latest.values() if v)

        out.append(
            schemas.SessionListItem(
                id=sess.id,
                title=sess.title,
                document_id=sess.document_id,
                document_filename=fname,
                n_questions=int(sess.n_questions or 0),
                n_attempted=n_attempted,
                n_correct=n_correct,
                created_at=sess.created_at.isoformat() if sess.created_at else None,
                finished_at=sess.finished_at.isoformat() if sess.finished_at else None,
                test_type=sess.test_type,
                style=_session_style(sess),
            )
        )
    return out


@router.get("/sessions/{session_id}", response_model=schemas.SessionDetailResponse)
def session_detail(
    session_id: int, user_id: str = Query(...), db: Session = Depends(get_db)
):
    sess = db.get(models.QuizSession, session_id)
    if sess is None or sess.owner_id != user_id:
        raise HTTPException(404, "회차를 찾을 수 없습니다.")

    questions = (
        db.query(models.Question)
        .filter(models.Question.session_id == sess.id)
        .order_by(models.Question.id.asc())
        .all()
    )
    fname = None
    if sess.document_id:
        d = db.get(models.Document, sess.document_id)
        fname = d.filename if d else None

    detail_qs: list[schemas.SessionDetailQuestion] = []
    n_attempted = 0
    n_correct = 0
    for q in questions:
        last = (
            db.query(models.Attempt)
            .filter(
                models.Attempt.owner_id == user_id,
                models.Attempt.question_id == q.id,
            )
            .order_by(models.Attempt.attempted_at.desc())
            .first()
        )
        attempted = last is not None
        if attempted:
            n_attempted += 1
            if last.correct:
                n_correct += 1
        detail_qs.append(
            schemas.SessionDetailQuestion(
                id=q.id,
                type=q.type,
                stem=q.stem,
                choices=q.choices,
                topic_tag=q.topic_tag,
                attempted=attempted,
                correct=bool(last.correct) if attempted else None,
                user_answer=last.user_answer if attempted else None,
                excluded=bool(q.excluded),
                passage=q.passage,
                group_id=q.group_id,
            )
        )

    summary = schemas.SessionListItem(
        id=sess.id,
        title=sess.title,
        document_id=sess.document_id,
        document_filename=fname,
        n_questions=int(sess.n_questions or len(questions)),
        n_attempted=n_attempted,
        n_correct=n_correct,
        created_at=sess.created_at.isoformat() if sess.created_at else None,
        finished_at=sess.finished_at.isoformat() if sess.finished_at else None,
        test_type=sess.test_type,
        style=_session_style(sess),
    )
    return schemas.SessionDetailResponse(session=summary, questions=detail_qs)


class ExcludeRequest(BaseModel):
    user_id: str
    excluded: bool = True


class VerifyRequest(BaseModel):
    user_id: str


@router.post("/verify/{question_id}")
def verify_question_route(question_id: int, req: VerifyRequest, db: Session = Depends(get_db)):
    q = db.get(models.Question, question_id)
    if q is None or q.owner_id != req.user_id:
        raise HTTPException(404, "문제를 찾을 수 없습니다.")

    # 노트 청크 발췌 (이 문제의 source doc 기준 + 같은 세션에 묶인 다른 문서들도 부차로)
    raw_refs = retriever.find_references(
        db, q.document_id, q.stem, str(q.answer or ""), limit=5
    )
    note_source = "\n\n---\n\n".join(
        (r.get("snippet") or "") for r in raw_refs
    ) or ""

    try:
        result = verifier.verify_question(
            stem=q.stem,
            qtype=q.type,
            choices=q.choices,
            correct_answer=q.answer,
            rationale=q.rationale,
            note_source=note_source,
        )
    except LLMUnavailableError as e:
        raise HTTPException(503, str(e))

    if result is None:
        raise HTTPException(502, "검증 결과를 파싱하지 못했습니다. 다시 시도하세요.")

    # mcq 면 보기 텍스트도 함께 반환
    if q.type == "mcq" and isinstance(q.answer, int) and q.choices and 0 <= q.answer < len(q.choices):
        result["stored_answer_text"] = q.choices[q.answer]

    result["question_id"] = q.id
    result["stored_answer"] = q.answer
    return result


@router.post("/exclude/{question_id}")
def set_exclude(question_id: int, req: ExcludeRequest, db: Session = Depends(get_db)):
    q = db.get(models.Question, question_id)
    if q is None or q.owner_id != req.user_id:
        raise HTTPException(404, "문제를 찾을 수 없습니다.")
    q.excluded = bool(req.excluded)
    db.commit()
    return {"question_id": question_id, "excluded": q.excluded}


@router.delete("/sessions/{session_id}")
def session_delete(
    session_id: int, user_id: str = Query(...), db: Session = Depends(get_db)
):
    sess = db.get(models.QuizSession, session_id)
    if sess is None or sess.owner_id != user_id:
        raise HTTPException(404, "회차를 찾을 수 없습니다.")
    db.delete(sess)  # CASCADE 로 하위 questions 삭제
    db.commit()
    return {"deleted": session_id}
