import hashlib

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from .. import models, schemas
from ..config import settings
from ..db import get_db
from ..deps import ensure_user
from ..services.extractor import chunk_text, extract_text


router = APIRouter(prefix="/api", tags=["upload"])


@router.post("/upload", response_model=schemas.UploadResponse)
async def upload(
    user_id: str = Form(...),
    test_type: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    user_id = (user_id or "").strip()
    test_type = (test_type or "").strip()
    if not user_id:
        raise HTTPException(400, "user_id 가 비어 있습니다. 먼저 로그인하세요.")
    if not test_type:
        raise HTTPException(400, "test_type (테스트 타입) 을 먼저 선택/입력하세요.")

    data = await file.read()
    if not data:
        raise HTTPException(400, "빈 파일입니다.")
    if len(data) > settings.MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"파일이 너무 큽니다 (최대 {settings.MAX_UPLOAD_MB}MB).")

    try:
        ensure_user(db, user_id)
    except ValueError as e:
        raise HTTPException(400, str(e))

    fname = file.filename or "uploaded"
    content_hash = hashlib.sha256(data).hexdigest()

    # (사용자 × 테스트 타입) 안에서 (파일명 OR 내용 해시) 중복이면 거절
    existing = (
        db.query(models.Document)
        .filter(
            models.Document.owner_id == user_id,
            models.Document.test_type == test_type,
            (models.Document.filename == fname)
            | (models.Document.content_hash == content_hash),
        )
        .order_by(models.Document.uploaded_at.desc())
        .first()
    )
    if existing is not None:
        same_name = existing.filename == fname
        same_hash = existing.content_hash == content_hash
        reason = (
            "동일 파일명+내용" if same_name and same_hash
            else "동일 파일명" if same_name
            else "동일 내용(다른 파일명)"
        )
        raise HTTPException(
            status_code=409,
            detail={
                "message": f"테스트 타입 '{test_type}' 에 이미 등록된 강의노트입니다 ({reason}).",
                "existing_document_id": int(existing.id),
                "existing_filename": existing.filename,
                "test_type": test_type,
                "uploaded_at": existing.uploaded_at.isoformat() if existing.uploaded_at else None,
            },
        )

    try:
        text = extract_text(fname, file.content_type, data)
    except Exception as e:
        raise HTTPException(415, f"파일 텍스트 추출에 실패했습니다: {e}")

    chunks = list(chunk_text(text, size=settings.CHUNK_SIZE))
    if not chunks:
        raise HTTPException(422, "파일에서 추출할 수 있는 텍스트가 없습니다.")

    doc = models.Document(
        owner_id=user_id,
        test_type=test_type,
        filename=fname,
        mime=file.content_type,
        content_hash=content_hash,
    )
    db.add(doc)
    db.flush()

    for i, c in enumerate(chunks):
        db.add(models.Chunk(document_id=doc.id, seq=i, content=c))

    db.commit()
    return schemas.UploadResponse(
        document_id=doc.id,
        filename=doc.filename,
        chunk_count=len(chunks),
    )


@router.get("/users")
def list_users(db: Session = Depends(get_db)):
    """지금까지 등록된 카테고리(test type) 핸들 목록.

    프론트의 카테고리 입력 datalist 자동완성에 사용한다.
    """
    rows = (
        db.query(models.User.id, models.User.created_at)
        .order_by(models.User.created_at.desc())
        .limit(200)
        .all()
    )
    return [
        {"id": r.id, "created_at": r.created_at.isoformat() if r.created_at else None}
        for r in rows
    ]


@router.get("/documents")
def list_documents(user_id: str, db: Session = Depends(get_db)):
    rows = (
        db.query(models.Document)
        .filter(models.Document.owner_id == user_id)
        .order_by(models.Document.uploaded_at.desc())
        .all()
    )
    return [
        {
            "id": r.id,
            "filename": r.filename,
            "uploaded_at": r.uploaded_at.isoformat() if r.uploaded_at else None,
        }
        for r in rows
    ]
