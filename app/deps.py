from sqlalchemy.orm import Session

from . import models


def ensure_user(db: Session, user_id: str) -> models.User:
    user_id = (user_id or "").strip()
    if not user_id:
        raise ValueError("user_id 가 비어 있습니다.")
    user = db.get(models.User, user_id)
    if user is None:
        user = models.User(id=user_id)
        db.add(user)
        db.flush()
    return user
