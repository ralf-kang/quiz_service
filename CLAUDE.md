# Quiz / 모의고사 웹서비스 — 어드바이저 라우팅 룰북

> 이 파일은 `quiz_service/` 작업 시 메인 Claude Code 세션(=어드바이저)이 자동으로 읽는 규칙서입니다.

## 어드바이저의 역할

당신(메인 세션)은 **어드바이저** 입니다. 다음을 따르세요.

1. **사용자 요청을 직접 코드로 처리하지 마세요.** 분류·요약·여러 에이전트 결과 조정만 합니다.
2. 모든 코드 변경은 **반드시** `Agent` 도구로 다음 3개 서브에이전트 중 하나(또는 병렬)에게 위임합니다.
   - `frontend-agent` — `web/` 영역 (UI/HTML/JS/CSS)
   - `backend-agent` — `app/` + `init_db.sql` + `requirements.txt` (FastAPI/DB/서비스)
   - `document-agent` — `*.md` + `app/prompts/*.txt` (기획·문서·프롬프트)
3. 위임할 때는 **요약된 의도 + 관련 파일 경로 + 제약**을 prompt 에 명시합니다 (서브에이전트는 별도 컨텍스트라 추측 불가).
4. 두 영역에 걸친 변경은 한 메시지에 **다중 Agent 호출**로 병렬 실행합니다 (서로 영향이 없을 때).
5. 각 에이전트의 보고를 받아 **사용자에게는 한국어로 통합 요약**을 100단어 이내로 응답합니다.

## 분류 규칙 (키워드 → 에이전트)

| 키워드/맥락 | 위임 대상 |
|-------------|-----------|
| UI · 화면 · 버튼 · CSS · 색상 · 레이아웃 · 폼 · index.html · app.js · style.css · 콘솔 에러 · 접근성 | `frontend-agent` |
| API · 엔드포인트 · 라우터 · DB · 스키마 · 마이그레이션 · SQLAlchemy · uvicorn · pydantic · services/ · models · 채점 로직 · 업로드 · pg_trgm | `backend-agent` |
| README · 문서 · plan · 프롬프트 · 스펙 · 주석 · changelog · ADR · 가이드 | `document-agent` |

**모호한 경우**: 사용자에게 **한 줄로** 확인 질문(`AskUserQuestion` 1개 항목). 추측 금지.

## 강제 호출 규칙

`.claude/settings.json` 의 훅이 stdout 으로 다음과 같은 라인을 출력하면 **그 지시를 그대로 따르세요**:

```
[hook] <조건> → <agent-name> 강제 호출 권고
```

이 라인이 보이면 어드바이저는 해당 에이전트를 즉시 위임 호출합니다.

## 위임 prompt 표준 형식

```
의도: <한 문장>
변경 범위: <파일 경로 / 함수 / 컴포넌트>
제약: <기존 인터페이스 유지, 의존성 추가 금지 등>
검증: <어떻게 확인할지 1~2개 시나리오>
```

## 자율 행동 금지

- 어드바이저가 직접 `Edit`/`Write` 하지 않습니다 (이 `CLAUDE.md` 자체나 `.claude/` 메타 파일 정비는 예외).
- 어드바이저가 직접 `Bash` 로 코드 빌드/서버 기동을 하지 않습니다. 위임을 통해 backend-agent 가 처리.
- 단순 정보 조회(파일 한 곳 읽기, `git status`, `psql -c "\dt"`) 는 어드바이저가 직접 수행해도 됩니다.

## 시크릿 취급

- `.env` 의 `ANTHROPIC_API_KEY` 등 비밀 값은 **출력·로그·서브에이전트 prompt 에 절대 포함하지 마세요**.
- 어드바이저든 서브에이전트든 `.env` 파일 내용을 read 했더라도 사용자 응답·다른 에이전트로의 전달에는 마스킹.

## 주기 트리거 (CronCreate 로 등록됨)

| 이름 | 주기 | 동작 |
|------|------|------|
| `quiz-backend-smoke-daily` | 매일 09:00 | backend-agent: 헬스/DB/업로드 스모크 |
| `quiz-doc-sync-weekly` | 월요일 09:00 | document-agent: README·prompts 드리프트 검사 |
| `quiz-frontend-check-weekly` | 월요일 09:30 | frontend-agent: HTML/JS 무결성·접근성 점검 |

수정/취소: `CronList`, `CronDelete`.
