from sqlalchemy import (
    BigInteger, Boolean, Column, ForeignKey, Integer, String, Text, TIMESTAMP, func,
)
from sqlalchemy.dialects.postgresql import JSONB

from .db import Base


class User(Base):
    __tablename__ = "users"
    id = Column(Text, primary_key=True)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())


class Document(Base):
    __tablename__ = "documents"
    id = Column(BigInteger, primary_key=True)
    owner_id = Column(Text, ForeignKey("users.id", ondelete="CASCADE"))
    test_type = Column(Text)
    filename = Column(Text, nullable=False)
    mime = Column(Text)
    content_hash = Column(Text)
    topics = Column(JSONB)                 # LLM 으로 추출한 핵심 토픽 (캐시)
    uploaded_at = Column(TIMESTAMP(timezone=True), server_default=func.now())


class ExamScope(Base):
    __tablename__ = "exam_scopes"
    test_type = Column(Text, primary_key=True)
    source = Column(Text)                  # 'file' | 'llm'
    topics = Column(JSONB)                 # [{name, section, ref_url}]
    fetched_at = Column(TIMESTAMP(timezone=True), server_default=func.now())


class Chunk(Base):
    __tablename__ = "chunks"
    id = Column(BigInteger, primary_key=True)
    document_id = Column(BigInteger, ForeignKey("documents.id", ondelete="CASCADE"))
    seq = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)


class QuizSession(Base):
    __tablename__ = "quiz_sessions"
    id = Column(BigInteger, primary_key=True)
    owner_id = Column(Text, ForeignKey("users.id", ondelete="CASCADE"))
    document_id = Column(BigInteger, ForeignKey("documents.id", ondelete="CASCADE"))
    document_ids = Column(JSONB)   # 다중 문서 출제 시 사용된 모든 doc id
    test_type = Column(Text)
    title = Column(Text)
    n_questions = Column(Integer, default=0)
    options = Column(JSONB)        # 출제 옵션 캐시 (style/styles, type_mix 등)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    finished_at = Column(TIMESTAMP(timezone=True))


class Question(Base):
    __tablename__ = "questions"
    id = Column(BigInteger, primary_key=True)
    owner_id = Column(Text, ForeignKey("users.id", ondelete="CASCADE"))
    document_id = Column(BigInteger, ForeignKey("documents.id", ondelete="CASCADE"))
    session_id = Column(BigInteger, ForeignKey("quiz_sessions.id", ondelete="CASCADE"))
    type = Column(Text, nullable=False)            # 'mcq' | 'short'
    style = Column(Text)
    stem = Column(Text, nullable=False)
    choices = Column(JSONB)
    answer = Column(JSONB, nullable=False)
    rationale = Column(Text)
    refs = Column(JSONB)
    topic_tag = Column(Text)
    excluded = Column(Boolean, default=False, nullable=True)
    passage = Column(Text)
    group_id = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())


class Attempt(Base):
    __tablename__ = "attempts"
    id = Column(BigInteger, primary_key=True)
    owner_id = Column(Text, ForeignKey("users.id", ondelete="CASCADE"))
    question_id = Column(BigInteger, ForeignKey("questions.id", ondelete="CASCADE"))
    user_answer = Column(JSONB)
    correct = Column(Boolean, nullable=False)
    feedback = Column(Text)
    attempted_at = Column(TIMESTAMP(timezone=True), server_default=func.now())


class Mastery(Base):
    __tablename__ = "mastery"
    owner_id = Column(Text, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    topic_tag = Column(Text, primary_key=True)
    correct_streak = Column(Integer, default=0)
    wrong_count = Column(Integer, default=0)
    level = Column(Integer, default=0)
    updated_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now())
