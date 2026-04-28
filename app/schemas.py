from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------- Upload ----------

class UploadResponse(BaseModel):
    document_id: int
    filename: str
    chunk_count: int


# ---------- Quiz generation ----------

class TypeMix(BaseModel):
    mcq: int = 4
    short: int = 1


class GenerateRequest(BaseModel):
    user_id: str
    document_id: Optional[int] = None              # 단일 문서 (legacy)
    document_ids: Optional[list[int]] = None       # 다중 문서 (출제 범위)
    n: int = 5
    type_mix: TypeMix = Field(default_factory=TypeMix)
    style: str = "기출"
    extra_instructions: Optional[str] = None

    def resolved_document_ids(self) -> list[int]:
        if self.document_ids:
            return [int(x) for x in self.document_ids if x is not None]
        if self.document_id is not None:
            return [int(self.document_id)]
        return []


class QuestionOut(BaseModel):
    id: int
    type: str
    style: Optional[str] = None
    stem: str
    choices: Optional[list[str]] = None
    topic_tag: Optional[str] = None
    session_id: Optional[int] = None
    excluded: bool = False
    passage: Optional[str] = None
    group_id: Optional[str] = None


class GenerateResponse(BaseModel):
    session_id: Optional[int] = None
    questions: list[QuestionOut]


# ---------- Answer (즉시 채점, 해설은 보류) ----------

class AnswerRequest(BaseModel):
    user_id: str
    question_id: int
    user_answer: Any                # int (mcq index) or str (short answer)


class Reference(BaseModel):
    document_id: int
    chunk_id: int
    snippet: str


class SubmittedResponse(BaseModel):
    """답안 제출 직후 응답 — 채점 결과·해설 모두 비공개. '제출됨' 표시만."""
    submitted: bool = True
    question_id: int
    attempt_id: int


class ExplainResponse(BaseModel):
    """사용자가 명시적으로 해설을 요청했을 때 노출."""
    question_id: int
    correct: bool
    correct_answer: Any
    rationale: Optional[str] = None
    references: list[Reference] = []
    mastery_level: int = 0
    topic_tag: Optional[str] = None
    user_answer: Any = None


# ---------- Similar / practice ----------

class SimilarRequest(BaseModel):
    user_id: str
    question_id: int


class PracticeRequest(BaseModel):
    user_id: str
    n: int = 3


# ---------- Wrong list ----------

class WrongItem(BaseModel):
    question_id: int
    stem: str
    type: str
    topic_tag: Optional[str] = None
    wrong_count: int
    level: int
    last_wrong_at: Optional[str] = None     # 가장 최근 오답 attempt 의 ISO8601 시각


class WrongListResponse(BaseModel):
    user_id: str
    items: list[WrongItem]
    topics: list[dict]              # [{topic_tag, level, wrong_count}]


# ---------- Quiz sessions (풀이 이력) ----------

class SessionListItem(BaseModel):
    id: int
    title: Optional[str] = None
    document_id: Optional[int] = None
    document_filename: Optional[str] = None
    n_questions: int = 0
    n_attempted: int = 0
    n_correct: int = 0
    created_at: Optional[str] = None
    finished_at: Optional[str] = None
    test_type: Optional[str] = None         # 사이드바 그룹용
    style: str = ""                          # quiz_sessions.options.styles|style; 없으면 ""


class QuestionReplay(BaseModel):
    """동일 문제 재풀이용 페이로드 — 정답·근거는 절대 노출하지 않음."""
    id: int
    type: str
    style: Optional[str] = None
    stem: str
    choices: Optional[list[str]] = None
    topic_tag: Optional[str] = None
    session_id: Optional[int] = None
    excluded: bool = False
    passage: Optional[str] = None
    group_id: Optional[str] = None


class SessionDetailQuestion(BaseModel):
    id: int
    type: str
    stem: str
    choices: Optional[list[str]] = None
    topic_tag: Optional[str] = None
    attempted: bool = False
    correct: Optional[bool] = None
    user_answer: Any = None
    excluded: bool = False
    passage: Optional[str] = None
    group_id: Optional[str] = None


class SessionDetailResponse(BaseModel):
    session: SessionListItem
    questions: list[SessionDetailQuestion]


# ---------- Auth ----------

class AuthStatus(BaseModel):
    configured: bool
    source: Optional[str] = None


class AuthLogin(BaseModel):
    api_key: str
