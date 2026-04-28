# Quiz / 모의고사 웹서비스

강의노트(PDF / DOCX / MD / TXT)를 업로드하면 **Claude AI**로 기출문제 스타일 모의고사를 생성·채점·근거 제시하는 1인용 로컬 웹서비스입니다.

---

## 주요 기능

| 기능 | 설명 |
|------|------|
| 📄 강의노트 업로드 | PDF·DOCX·MD·TXT 다중 업로드, 중복 방지(SHA-256) |
| 🎯 모의고사 생성 | 난이도 4단계, 스타일 칩(기출·암기·사례형 등), 지문 블록(passage) 출제 |
| ✅ 1문항 채점 | 답안 제출 즉시 채점·저장, 해설은 [🔍 해설 보기] 클릭 시 노출 |
| ⚖️ 정답 검증 | 강의노트 본문 + 공식 자료 기준 2-way 검증 |
| 📊 대시보드 | Test Type별 정답률·약점 토픽·최근 활동 위젯 |
| 📚 시험범위 분석 | 공식 시험 범위 vs 강의노트 커버리지 비교(갭/추가 분류) |
| 🔁 회차 이력 | 출제→풀이를 한 회차로 저장, 재조회 가능 |
| 💡 약점 강화 | 5단계 체득(mastery) 기반 약점 유형 집중 출제 |
| 🔑 Claude 인증 | 로컬 CLI 자격 자동 탐색 또는 API Key / OAuth 토큰 수동 입력 |

---

## 스크린샷 & 아키텍처

```
quiz_service/
├── app/                      # FastAPI 백엔드
│   ├── main.py               # 앱 진입점
│   ├── config.py / db.py / models.py / schemas.py / deps.py
│   ├── routers/              # auth · upload · quiz · wrong · admin
│   ├── services/             # auth · extractor · llm · retriever · quiz_gen
│   │                         # grader · mastery · verifier · scope_analyzer
│   ├── prompts/              # generate.txt · grade.txt
│   │   └── style_packs/      # 시험별 기출 스타일 팩 (정보처리기사·ISMS-P·정보보안기사·default)
│   └── exam_scope/           # 시험 범위 시드 마크다운
├── web/                      # 프론트엔드 (Vanilla JS / 단일 페이지)
│   ├── index.html
│   ├── app.js
│   └── style.css
├── init_db.sql               # PostgreSQL 스키마 (idempotent)
├── requirements.txt
├── .env.example
├── CLAUDE.md                 # 멀티 에이전트 룰북
└── .claude/                  # 에이전트 정의 + 훅
    ├── agents/               # frontend / backend / document
    └── hooks/                # route_by_keyword.sh · route_by_path.sh
```

---

## 사전 조건

| 항목 | 버전 |
|------|------|
| Python | 3.12+ |
| PostgreSQL | 15 이상 (로컬 실행) |

---

## 설치 & 실행

### 1. 저장소 클론

```bash
git clone https://github.com/ralf-kang/quiz_service.git
cd quiz_service
```

### 2. PostgreSQL DB 생성

```sql
-- psql 로 접속 후
CREATE USER webservice_user WITH PASSWORD 'webservice_pass';
CREATE DATABASE webservice_db OWNER webservice_user;
GRANT ALL PRIVILEGES ON DATABASE webservice_db TO webservice_user;
```

> DB 이름·계정은 `.env` 에서 자유롭게 변경 가능합니다.

### 3. 스키마 적용

```bash
psql -d webservice_db -U webservice_user -h localhost -f init_db.sql
```

### 4. 파이썬 환경 & 의존성

```bash
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 5. 환경 변수 설정

```bash
cp .env.example .env
# 필요 시 편집
```

`.env` 주요 항목:

```env
DATABASE_URL=postgresql+psycopg2://webservice_user:webservice_pass@localhost:5432/webservice_db
ANTHROPIC_API_KEY=          # 비워도 서버 기동 가능 (UI에서 나중에 등록)
ANTHROPIC_MODEL=claude-sonnet-4-6
MAX_UPLOAD_MB=50
CHUNK_SIZE=1000
```

> `ANTHROPIC_API_KEY` 는 서버 시작 후 UI 우측 상단 **[🔑 Claude 로그인]** 버튼으로도 등록할 수 있습니다.

### 6. 서버 실행

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

| URL | 설명 |
|-----|------|
| http://localhost:8000/ | 웹 UI |
| http://localhost:8000/docs | Swagger API 문서 |
| http://localhost:8000/health | 헬스 체크 |

---

## Claude 인증 방법

두 가지 방법을 지원합니다.

1. **자동 로그인** — [🔑 Claude 로그인] 클릭 시 로컬 PC의 Claude CLI 자격(`~/.claude/.credentials.json`)을 자동 탐색·채택. Claude Code 사용자는 추가 입력 불필요.
2. **수동 입력** — 모달에서 토큰 직접 붙여넣기
   - `sk-ant-api03-…` : Anthropic API Key
   - `sk-ant-oat01-…` : Pro/Max 플랜 OAuth 토큰 (`claude setup-token` 으로 발급)

> 토큰은 **서버 메모리에만 보관**됩니다. 디스크·DB 저장 없음. 서버 재기동 시 재입력 필요.

---

## 사용 흐름

```
로그인 (사용자 ID 입력)
  └─ Test Type 선택 / 신규 생성
       └─ 강의노트 다중 업로드 (PDF/DOCX/MD/TXT)
            └─ 출제 옵션 설정 (난이도·스타일 칩·범위 체크)
                 └─ [출제하기]
                      └─ 문항별 답안 제출 → 채점
                           └─ [🔍 해설 보기] → 정답·근거·인용문 노출
                                └─ [⚖️ 정답 검증] (선택)
```

---

## API 엔드포인트

| Method | 경로 | 설명 |
|--------|------|------|
| GET | `/health` | 서버 헬스 + LLM 인증 여부 |
| GET | `/api/auth/claude` | Claude 인증 상태 |
| POST | `/api/auth/claude/login` | 자동 로그인 (CLI 자격 자동 탐색) |
| POST | `/api/auth/claude` | API Key / OAuth Token 직접 등록 |
| DELETE | `/api/auth/claude` | 메모리에서 토큰 제거 |
| GET | `/api/users` | Test Type 핸들 목록 (자동완성용) |
| POST | `/api/upload` | 강의노트 업로드 |
| GET | `/api/documents` | 사용자별 업로드 문서 목록 |
| GET | `/api/quiz/test-types` | 사용자 Test Type 목록 |
| GET | `/api/quiz/tree` | 사이드바 트리 (test_type → document → session) |
| GET | `/api/quiz/dashboard` | 학습 통계 대시보드 |
| POST | `/api/quiz/generate` | 모의고사 생성 |
| POST | `/api/quiz/answer` | 답안 제출 → 채점 |
| GET | `/api/quiz/explain/{qid}` | 정답·근거·레퍼런스 조회 |
| POST | `/api/quiz/verify/{qid}` | 정답 2-way 검증 |
| POST | `/api/quiz/similar` | 유사 문제 추가 출제 |
| GET | `/api/quiz/sessions` | 풀이 회차 목록 |
| GET | `/api/quiz/sessions/{sid}` | 회차 상세 |
| DELETE | `/api/quiz/sessions/{sid}` | 회차 삭제 |
| GET | `/api/wrong/list` | 약점 노트 |
| POST | `/api/wrong/practice` | 약점 강화 학습 출제 |
| DELETE | `/api/wrong/{qid}` | 익힌 문제 삭제 |
| DELETE | `/api/user/{user_id}` | 사용자 데이터 일괄 삭제 |

---

## 데이터 모델

`init_db.sql` 참고.

| 테이블 | 역할 |
|--------|------|
| `users` | 사용자 핸들 |
| `documents` / `chunks` | 강의노트 원문 + pg_trgm 인덱스 |
| `quiz_sessions` | 출제→풀이 회차 |
| `questions` | 문항 (passage·group_id·excluded 포함) |
| `attempts` | 답안 제출 이력 |
| `mastery` | `(owner_id, topic_tag)` 별 5단계 체득 |
| `exam_scopes` | 시험 범위 LLM 캐시 |

---

## 한계

- **PDF 표·이미지**: 텍스트 레이어 기반 추출이라 복잡한 표·다이어그램·이미지의 의미는 손실됩니다. 표·이미지 감지 시 `[TABLE]` / `[IMAGE]` 마커로 자리만 표시됩니다.
- **권장**: 텍스트 위주의 강의노트(MD/TXT/DOCX 또는 정리된 PDF) 사용. 스캔본은 OCR 후 업로드.

---

## 기술 스택

| 영역 | 기술 |
|------|------|
| Backend | FastAPI, SQLAlchemy 2.0, Pydantic v2, Uvicorn |
| Database | PostgreSQL 15+ (pg_trgm, gin index) |
| AI | Anthropic Claude API (claude-sonnet-4-6 기본) |
| Frontend | Vanilla JS, HTML5, CSS3 (단일 페이지) |
| 문서 추출 | pdfplumber, pypdf, python-docx |

---

## 라이선스

MIT
