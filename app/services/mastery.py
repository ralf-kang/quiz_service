"""Track per-(user, topic_tag) 5-stage mastery."""
from sqlalchemy.orm import Session

from .. import models


def update(db: Session, *, user_id: str, topic_tag: str | None, correct: bool) -> int:
    """Update mastery for a topic and return the new level (0~5).

    Rules:
    - correct → correct_streak += 1, level = min(5, streak)
    - wrong   → streak reset to 0, wrong_count += 1, level = max(0, level - 1)
    """
    if not topic_tag:
        return 0

    row = (
        db.query(models.Mastery)
        .filter(models.Mastery.owner_id == user_id, models.Mastery.topic_tag == topic_tag)
        .one_or_none()
    )
    if row is None:
        row = models.Mastery(
            owner_id=user_id,
            topic_tag=topic_tag,
            correct_streak=0,
            wrong_count=0,
            level=0,
        )
        db.add(row)

    if correct:
        row.correct_streak = (row.correct_streak or 0) + 1
        row.level = min(5, row.correct_streak)
    else:
        row.correct_streak = 0
        row.wrong_count = (row.wrong_count or 0) + 1
        row.level = max(0, (row.level or 0) - 1)

    db.flush()
    return int(row.level)
