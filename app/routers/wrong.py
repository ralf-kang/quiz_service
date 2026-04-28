from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import models, schemas
from ..db import get_db
from ..deps import ensure_user
from ..services import quiz_gen
from ..services.llm import LLMUnavailableError


router = APIRouter(prefix="/api", tags=["wrong"])


@router.get("/wrong/list", response_model=schemas.WrongListResponse)
def wrong_list(user_id: str = Query(...), db: Session = Depends(get_db)):
    # Per-question wrong count: most recent attempt was wrong (and never recovered)
    # last_wrong_at: 최근 오답(correct=false) attempt 의 attempted_at — 카드 정렬용
    sub = (
        db.query(
            models.Attempt.question_id.label("qid"),
            func.count().filter(models.Attempt.correct == False).label("wcount"),  # noqa: E712
            func.max(models.Attempt.attempted_at).label("last_at"),
            func.max(models.Attempt.attempted_at)
                .filter(models.Attempt.correct == False)  # noqa: E712
                .label("last_wrong_at"),
        )
        .filter(models.Attempt.owner_id == user_id)
        .group_by(models.Attempt.question_id)
        .subquery()
    )

    rows = (
        db.query(models.Question, sub.c.wcount, sub.c.last_wrong_at)
        .join(sub, sub.c.qid == models.Question.id)
        .filter(
            sub.c.wcount > 0,
            (models.Question.excluded.is_(False)) | (models.Question.excluded.is_(None)),
        )
        .order_by(sub.c.last_at.desc())
        .all()
    )

    items: list[schemas.WrongItem] = []
    for q, wcount, last_wrong_at in rows:
        m = (
            db.query(models.Mastery)
            .filter(
                models.Mastery.owner_id == user_id,
                models.Mastery.topic_tag == q.topic_tag,
            )
            .one_or_none()
        )
        items.append(
            schemas.WrongItem(
                question_id=q.id,
                stem=q.stem,
                type=q.type,
                topic_tag=q.topic_tag,
                wrong_count=int(wcount or 0),
                level=int(m.level) if m else 0,
                last_wrong_at=last_wrong_at.isoformat() if last_wrong_at else None,
            )
        )

    topics = (
        db.query(models.Mastery)
        .filter(models.Mastery.owner_id == user_id)
        .order_by(models.Mastery.level.asc(), models.Mastery.wrong_count.desc())
        .all()
    )
    topic_payload = [
        {
            "topic_tag": t.topic_tag,
            "level": int(t.level or 0),
            "wrong_count": int(t.wrong_count or 0),
            "correct_streak": int(t.correct_streak or 0),
        }
        for t in topics
    ]

    return schemas.WrongListResponse(user_id=user_id, items=items, topics=topic_payload)


@router.post("/wrong/practice", response_model=schemas.GenerateResponse)
def wrong_practice(req: schemas.PracticeRequest, db: Session = Depends(get_db)):
    try:
        ensure_user(db, req.user_id)
    except ValueError as e:
        raise HTTPException(400, str(e))

    weakest = (
        db.query(models.Mastery)
        .filter(models.Mastery.owner_id == req.user_id, models.Mastery.level < 5)
        .order_by(models.Mastery.level.asc(), models.Mastery.wrong_count.desc())
        .first()
    )
    if weakest is None:
        raise HTTPException(404, "강화할 약점 유형이 아직 없습니다. 먼저 문제를 풀어 주세요.")

    base_q = (
        db.query(models.Question)
        .filter(
            models.Question.owner_id == req.user_id,
            models.Question.topic_tag == weakest.topic_tag,
            (models.Question.excluded.is_(False)) | (models.Question.excluded.is_(None)),
        )
        .order_by(models.Question.created_at.desc())
        .first()
    )
    if base_q is None:
        raise HTTPException(404, "해당 유형의 원본 문제를 찾을 수 없습니다.")

    n = max(1, min(10, int(req.n)))
    try:
        items = quiz_gen.generate_questions(
            db,
            user_id=req.user_id,
            document_id=base_q.document_id,
            n=n,
            type_mix={"mcq": max(0, n - 1), "short": min(1, n)},
            style=base_q.style or "기출",
            extra_instructions=f"`{weakest.topic_tag}` 유형 약점 강화. 표면 변형이 아니라 이해를 검증하는 변형 문제로 출제.",
            topic_hint=weakest.topic_tag,
        )
    except LLMUnavailableError as e:
        raise HTTPException(503, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))

    db.commit()
    return schemas.GenerateResponse(
        questions=[
            schemas.QuestionOut(
                id=q.id,
                type=q.type,
                style=q.style,
                stem=q.stem,
                choices=q.choices,
                topic_tag=q.topic_tag,
            )
            for q in items
        ]
    )


@router.delete("/wrong/{question_id}")
def wrong_delete(question_id: int, user_id: str = Query(...), db: Session = Depends(get_db)):
    q = db.get(models.Question, question_id)
    if q is None or q.owner_id != user_id:
        raise HTTPException(404, "문제를 찾을 수 없습니다.")
    db.delete(q)
    db.commit()
    return {"deleted": question_id}


@router.delete("/user/{user_id}")
def user_delete(user_id: str, db: Session = Depends(get_db)):
    u = db.get(models.User, user_id)
    if u is None:
        raise HTTPException(404, "사용자가 없습니다.")
    db.delete(u)  # CASCADE clears documents/chunks/questions/attempts/mastery
    db.commit()
    return {"deleted": user_id}
