"""기존 풀이 이력이 있는 (user_id, test_type) 조합에 대해 주기적으로 새 문제를 생성.

스케줄러 (APScheduler) 가 `run_auto_round` 를 호출하면 다음 흐름으로 진행한다:
  1. `iter_targets` 로 후보 (user_id, test_type, doc_ids) 목록 수집.
  2. round-robin 커서로 회전하며 limit 만큼만 처리 — 매 cycle 다른 대상 우선.
  3. 각 대상마다 독립 SessionLocal 트랜잭션 + try/except — 한 대상 실패가 다른 대상을 막지 않음.
  4. LLM 호출은 `quiz_gen.generate_questions` 만 사용. 결과를 새 `quiz_sessions` 행으로 저장하며
     `options.auto = True`, `options.scheduled_at` 메타데이터 부여.

이 모듈은 Anthropic SDK 를 직접 호출하지 않는다.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import models
from ..db import SessionLocal
from ..deps import ensure_user
from . import auth, quiz_gen
from .llm import LLMUnavailableError, set_loopback

logger = logging.getLogger(__name__)


# 회전 처리용 in-memory cursor — round 가 호출될 때마다 진행 위치 기억
_cursor_lock = threading.Lock()
_cursor: int = 0


# ---------- targets 수집 ----------

def iter_targets(db: Session) -> list[tuple[str, str, list[int]]]:
    """quiz_sessions 가 1건 이상 있는 모든 user_id 를 대상으로,
    그 user 가 사용해 본 distinct test_type 별로 출제에 사용할 doc_ids 를 반환.

    doc_ids 우선순위:
      1) 가장 최근 (user, test_type) 세션의 `document_ids` (JSONB list).
      2) 비어있으면 그 세션의 단일 `document_id` 를 list 로 감싼다.
      3) 그것도 비면 documents 테이블에서 (user, test_type) 의 가장 최근 업로드 1~3건.
    """
    rows = (
        db.query(models.QuizSession.owner_id, models.QuizSession.test_type)
        .filter(models.QuizSession.test_type.isnot(None))
        .group_by(models.QuizSession.owner_id, models.QuizSession.test_type)
        .all()
    )

    targets: list[tuple[str, str, list[int]]] = []
    for owner_id, test_type in rows:
        if not owner_id or not test_type:
            continue

        latest = (
            db.query(models.QuizSession)
            .filter(
                models.QuizSession.owner_id == owner_id,
                models.QuizSession.test_type == test_type,
            )
            .order_by(models.QuizSession.created_at.desc())
            .first()
        )

        doc_ids: list[int] = []
        if latest is not None:
            raw = latest.document_ids
            if isinstance(raw, list):
                doc_ids = [int(x) for x in raw if x is not None]
            if not doc_ids and latest.document_id is not None:
                doc_ids = [int(latest.document_id)]

        if not doc_ids:
            recent_docs = (
                db.query(models.Document.id)
                .filter(
                    models.Document.owner_id == owner_id,
                    models.Document.test_type == test_type,
                )
                .order_by(models.Document.uploaded_at.desc())
                .limit(3)
                .all()
            )
            doc_ids = [int(r[0]) for r in recent_docs]

        if not doc_ids:
            continue

        # doc 소유 검증 (현재 user 가 보유한 문서만 남김)
        owned = (
            db.query(models.Document.id)
            .filter(
                models.Document.owner_id == owner_id,
                models.Document.id.in_(doc_ids),
            )
            .all()
        )
        owned_set = {int(r[0]) for r in owned}
        doc_ids = [d for d in doc_ids if d in owned_set]
        if not doc_ids:
            continue

        targets.append((owner_id, test_type, doc_ids))

    return targets


# ---------- 옵션 추출 ----------

def _last_options(db: Session, user_id: str, test_type: str) -> dict:
    """해당 (user, test_type) 의 가장 최근 quiz_session.options 를 반환.

    auto 세션이 더 우선되지 않게, 사람이 만든 세션을 먼저 본 뒤 없으면 auto 세션이라도 사용.
    """
    sess = (
        db.query(models.QuizSession)
        .filter(
            models.QuizSession.owner_id == user_id,
            models.QuizSession.test_type == test_type,
        )
        .order_by(models.QuizSession.created_at.desc())
        .first()
    )
    if sess is None or not isinstance(sess.options, dict):
        return {}
    return dict(sess.options)


def _extract_style(opts: dict) -> str:
    v = opts.get("styles") or opts.get("style")
    if isinstance(v, list):
        joined = ", ".join(str(x).strip() for x in v if str(x).strip())
        return joined or "기출"
    if isinstance(v, str) and v.strip():
        return v.strip()
    return "기출"


def _extract_difficulty(opts: dict) -> str:
    extra = opts.get("extra_instructions") or ""
    if isinstance(extra, str) and "난이도" in extra:
        return extra
    diff = opts.get("difficulty")
    if isinstance(diff, str) and diff.strip():
        return f"난이도: {diff.strip()}"
    return "난이도: 중"


def _build_title(test_type: str, n: int) -> str:
    when = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    return f"{when} · 자동출제 · {test_type} · {n}문항"


# ---------- 1대상 처리 ----------

def _process_one(
    user_id: str,
    test_type: str,
    doc_ids: list[int],
    n_questions: int,
) -> tuple[bool, Optional[str], bool]:
    """단일 (user, test_type) 대상을 독립 트랜잭션으로 처리.

    반환: (processed_ok, error_message, cli_fallback_used).
        cli_fallback_used: 토큰 미등록 상태에서 generate 가 성공한 경우 True (CLI 패스스루로 처리됨).
    예외 시 rollback 하고 (False, msg, False) 반환.
    """
    db = SessionLocal()
    token_absent = not auth.is_configured()
    try:
        ensure_user(db, user_id)

        opts = _last_options(db, user_id, test_type)
        style = _extract_style(opts)
        extra = _extract_difficulty(opts)
        type_mix_raw = opts.get("type_mix") if isinstance(opts.get("type_mix"), dict) else {}
        try:
            mcq = int(type_mix_raw.get("mcq", n_questions))
        except (TypeError, ValueError):
            mcq = n_questions
        try:
            short = int(type_mix_raw.get("short", 0))
        except (TypeError, ValueError):
            short = 0
        if mcq + short != n_questions:
            mcq = max(0, n_questions - short)
            if mcq + short == 0:
                mcq = n_questions
        type_mix = {"mcq": mcq, "short": short}

        primary_doc = db.get(models.Document, doc_ids[0])
        if primary_doc is None:
            return (False, f"primary doc {doc_ids[0]} not found", False)

        scheduled_at = datetime.now(timezone.utc).isoformat()
        sess = models.QuizSession(
            owner_id=user_id,
            document_id=primary_doc.id,
            document_ids=doc_ids,
            test_type=test_type,
            title=_build_title(test_type, n_questions),
            n_questions=n_questions,
            options={
                "style": style,
                "type_mix": type_mix,
                "extra_instructions": extra,
                "auto": True,
                "scheduled_at": scheduled_at,
            },
        )
        db.add(sess)
        db.flush()

        items = quiz_gen.generate_questions(
            db,
            user_id=user_id,
            document_ids=doc_ids,
            n=n_questions,
            type_mix=type_mix,
            style=style,
            extra_instructions=extra,
        )
        for q in items:
            q.session_id = sess.id
        sess.n_questions = len(items)

        db.commit()
        logger.info(
            "auto_gen: ok user=%s test_type=%s docs=%s questions=%d session=%d",
            user_id, test_type, doc_ids, len(items), sess.id,
        )
        return (True, None, token_absent)
    except LLMUnavailableError as e:
        db.rollback()
        logger.warning("auto_gen: LLM unavailable user=%s test_type=%s err=%s", user_id, test_type, e)
        return (False, f"llm_unavailable: {e}", False)
    except Exception as e:  # noqa: BLE001 — 한 대상 실패가 다른 대상을 막지 않게
        db.rollback()
        logger.warning("auto_gen: error user=%s test_type=%s err=%s", user_id, test_type, e)
        return (False, f"{type(e).__name__}: {e}", False)
    finally:
        db.close()


# ---------- round 실행 ----------

def run_auto_round(limit: int = 5, n_questions: int = 3) -> dict:
    """모든 대상 중 round-robin 으로 최대 `limit` 개를 골라 1회 출제.

    반환:
        {
          "processed": int,           # 성공 출제 개수
          "skipped": int,             # 시도했으나 실패해 넘어간 개수
          "errors": [{"user_id":.., "test_type":.., "msg":..}, ...],
          "total_targets": int,       # 후보 전체 수
          "cli_fallback_used": int,   # 토큰 부재 상태에서 CLI 패스스루로 성공한 케이스 수
        }
    """
    global _cursor

    discovery = SessionLocal()
    try:
        targets = iter_targets(discovery)
    finally:
        discovery.close()

    total = len(targets)
    if total == 0:
        return {"processed": 0, "skipped": 0, "errors": [], "total_targets": 0, "cli_fallback_used": 0}

    # round-robin: cursor 부터 limit 개 윈도우 추출
    with _cursor_lock:
        start = _cursor % total
        _cursor = (start + min(limit, total)) % total
    if start + limit <= total:
        window = targets[start : start + limit]
    else:
        window = targets[start:] + targets[: (start + limit) - total]

    # 스케줄러/admin run 컨텍스트는 HTTP 미들웨어를 거치지 않아 _loopback_request 가
    # 세팅되지 않는다. 같은 머신에서 도는 in-process scheduler 한정으로 CLI 폴백을
    # 활성화하기 위해 이 라운드 동안만 loopback 플래그를 켰다가 finally 에서 복구한다.
    processed = 0
    skipped = 0
    cli_fallback_used = 0
    errors: list[dict] = []
    set_loopback(True)
    try:
        for user_id, test_type, doc_ids in window:
            ok, msg, used_cli = _process_one(user_id, test_type, doc_ids, n_questions)
            if ok:
                processed += 1
                if used_cli:
                    cli_fallback_used += 1
            else:
                skipped += 1
                errors.append({"user_id": user_id, "test_type": test_type, "msg": msg or "unknown"})
    finally:
        set_loopback(False)

    logger.info(
        "auto_gen: round done processed=%d skipped=%d total_targets=%d cursor=%d cli_fallback=%d",
        processed, skipped, total, _cursor, cli_fallback_used,
    )
    return {
        "processed": processed,
        "skipped": skipped,
        "errors": errors,
        "total_targets": total,
        "cli_fallback_used": cli_fallback_used,
    }
