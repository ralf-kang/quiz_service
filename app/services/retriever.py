"""Retrieve relevant chunks from PostgreSQL via pg_trgm similarity."""
import re

from sqlalchemy import text
from sqlalchemy.orm import Session


_NON_WORD = re.compile(r"[^\w가-힣]+", re.UNICODE)


def _query_text(*pieces: str) -> str:
    """Build a compact keyword string from question stem/answer."""
    joined = " ".join(p for p in pieces if p)
    joined = _NON_WORD.sub(" ", joined)
    tokens = [t for t in joined.split() if len(t) >= 2]
    # de-dup, keep order
    seen, out = set(), []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return " ".join(out[:30]) or joined[:200]


def find_references(
    db: Session,
    document_id: int,
    *pieces: str,
    limit: int = 3,
) -> list[dict]:
    q = _query_text(*pieces)
    if not q:
        return []

    sql = text(
        """
        SELECT id, document_id, content, similarity(content, :q) AS sim
        FROM chunks
        WHERE document_id = :doc_id
          AND content % :q
        ORDER BY sim DESC
        LIMIT :lim
        """
    )
    rows = db.execute(sql, {"q": q, "doc_id": document_id, "lim": limit}).fetchall()

    if not rows:
        # Fallback: pick the first few chunks if trigram match misses (short answers)
        sql_fallback = text(
            "SELECT id, document_id, content, 0.0 AS sim FROM chunks "
            "WHERE document_id = :doc_id ORDER BY seq LIMIT :lim"
        )
        rows = db.execute(sql_fallback, {"doc_id": document_id, "lim": limit}).fetchall()

    out = []
    for r in rows:
        snippet = (r.content or "")[:280]
        out.append(
            {
                "document_id": int(r.document_id),
                "chunk_id": int(r.id),
                "snippet": snippet,
            }
        )
    return out
