# Changelog

## 2026-04-26 (latest++++++++)

### Changed
- **기출 시나리오 출제 강화** — 모든 style pack(`정보보안기사`, `정보처리기사`, `ISMS-P`, `default`) 상단에 "시나리오 기반 출제 원칙" 명시. 가상 회사·시스템·프로젝트·요구사항을 stem 에 등장시켜 그 안의 위반·누락·오류를 식별하도록 유도.
- **PDF 표/이미지 마커 도입** — 추출 단계에서 표·이미지 영역에 `[TABLE]` / `[IMAGE]` 자리표시자 마커 적용. 한계는 README "한계" 섹션에 명문화.
- **사이드바·약점·대시보드 재설계** — 좌측 트리·약점 노트를 카드형 UI 로 재구성 (frontend 작업).
- **위젯 대시보드** — 우측 상단 대시보드를 test_type 별 정답률·약점·최근 활동 위젯 묶음으로 재배치.

## 2026-04-26 (latest+++++++)

### Added
- **출제 카운트다운을 버튼 인라인으로 이동.** 화면 가운데 패널 제거 — [출제하기]/[비슷한 문제]/[약점 강화 학습]/[⚖️ 정답 검증] 버튼이 직접 진행바·남은 초·"+N초 초과" 를 표시.
- **사용자 정의 스타일 콤보박스** — 입력으로 만든 스타일이 옆 콤보박스에 누적 노출되어 다음 출제에 빠르게 재선택. localStorage 영속화.
- **시험 범위 vs 강의노트 비교 대시보드** (`📚 시험 범위 분석`):
  - 시드 4종(`정보처리기사`, `ISMS-P`, `정보보안기사`, `default`) 의 공식 시험 범위 마크다운.
  - 파일 없으면 LLM 으로 시험 범위 fetch → DB(`exam_scopes`) 캐시.
  - 강의노트 토픽도 LLM 으로 추출해 `documents.topics` 캐시.
  - 우측에 **커버됨 / 갭(시험범위지만 노트 없음) / 추가(시험범위 밖)** 3열로 표시. 토픽별 출제 횟수·오답률 동시 노출.
- **문제·정답 재검증** (`POST /api/quiz/verify/{qid}`):
  - 카드의 [⚖️ 정답 검증] 버튼.
  - 두 갈래 검토 — (1) 강의노트 본문과의 일관성 (2) 공식 자료 기준 정답.
  - verdict: `ok / warning / conflict` + 제안 정답 + 신뢰도.

### Schema
- `documents.topics JSONB`, `quiz_sessions.document_ids JSONB`, `exam_scopes(test_type PK, source, topics, fetched_at)` 추가.

## 2026-04-26 (latest++++++)

### Added
- **다중 강의노트 동시 업로드** — `<input type="file" multiple>` + 프론트가 순차 호출. 진행 표시("n/m 업로드 중…") 와 결과 요약(성공·중복스킵·실패) 한 번에 보고.
- **출제 범위 체크박스 리스트** — 단일 셀렉트 → 테스트 타입의 모든 노트가 체크박스로 표시되며 디폴트 전체 체크. 옆의 [전체 체크 / 해제] 버튼으로 일괄 토글.
- 신규 업로드된 노트는 자동으로 체크 상태에 추가. 사용자가 명시적으로 해제하지 않으면 다음 출제에 포함.
- API: `POST /api/quiz/generate` 가 `document_ids: list[int]` 수용 (단일 `document_id` 도 backward-compat).
- DB: `quiz_sessions.document_ids JSONB` 신규 — 다중 문서 회차의 출처 보존.

### Changed
- LLM 컨텍스트가 `## SOURCE: <파일명> (doc_id=N)` 섹션으로 분할되어 균형 출제 + 인용 추적 향상. 한도 12,000 → 20,000자.
- LLM 응답 스키마에 선택 필드 `source_doc_id` 추가 — 문항이 어느 노트에서 나왔는지 표기.
- 회차 제목이 다중 문서일 때 `<첫 문서명> 외 N건` 으로 표시.

## 2026-04-26 (latest+++++)

### Added
- **해설 정확·요약화** — `[🔍 해설 보기]` 호출 시 LLM 으로 한 번 더 요약(2~4문장) + 원문 청크에서 60자 이내 인용문 1~3개를 발췌. 결과는 `questions.refs` 에 캐시되어 [다시 보기] 시 즉시 동일하게 표시.
- 인용문은 원문 그대로의 발췌만 허용(번역·재진술 금지). 매칭된 chunk_id 가 같이 표시되어 추적 가능.
- `app/services/explain_summarizer.py` 신규 — explain 전용 LLM 래퍼.

### Changed
- 난이도 **상**: passage 가 **최소 50문장 / ~1,500자 이상** + 여러 단락. 단순 암기로 풀 수 없도록 행간 추론·조건 분기·모순 탐지를 요구하는 문항 묶음(group) 강제.
- 난이도 **최상**: passage **70문장+** + 예외 케이스·반례·정량 비교 포함, **다단계 추론** 필요.
- 백엔드 검증: 응답이 위 기준에 미달이면 **자동 재요청**. `max_tokens` 도 상=9000, 최상=12000 으로 상향.
- 프론트 카운트다운 ETA 가 난이도별 가중치(상 ×2.2 / 최상 ×3.0) 반영.

## 2026-04-26 (latest++++)

## 2026-04-26 (latest+++)

### Added
- **출제 카운트다운 패널** — [출제하기]/[비슷한 문제]/[약점 강화 학습] 클릭 시 본문 위에 진행바 + "예상 X초 / 남은 Y초" 표시. 0초 도달 후에는 주황 톤으로 "거의 다 됐어요…" 와 초과 시간 안내.
- 직전 실 소요시간을 `localStorage["quiz_gen_timing"]` 에 지수이동평균으로 누적 — 다음 호출의 ETA 가 점점 정확해짐. 모드별(CLI/API/기출) 따로 학습.
- **난이도 옵션** (출제 옵션) — 하/중/상/최상 4단계. 선택 시 LLM 프롬프트의 EXTRA 에 `난이도: 상` 형태로 주입되어 함정·심화·변별력에 영향.

## 2026-04-26 (latest++)

### Added
- **시험별 기출 스타일 팩** 인프라. `app/prompts/style_packs/<test_type>.md` 의 마크다운이
  "기출" 스타일 선택 시 LLM 시스템 프롬프트에 자동 주입.
  - 시드 4종: `default`, `정보처리기사`, `ISMS-P`, `정보보안기사`.
  - 매칭 우선순위: 정확 일치 → 대소문자 무시 → 부분 매칭 → default.
  - 새 시험은 같은 디렉터리에 `<이름>.md` 1장 추가하면 즉시 반영.
- **지문 블록(passage) + 그룹(group_id) 출제** — 한 사례·코드·패킷 발췌(200~500자) 를 공유하는
  2~3문항 묶음 출제 가능. UI 가 회색 패널 안에 지문 + 묶인 문항을 렌더.
- 출제 옵션 (n / mcq / short / 스타일 칩 / 추가지시) 을 **localStorage 에 영속화** —
  테스트 타입 전환·새로고침 후에도 직전 값 유지.

### Changed
- `questions` 테이블에 `passage`, `group_id` 컬럼 추가 (둘 다 NULL 허용).
- 기출 스타일 출제 시 `max_tokens=6000` 으로 상향 (지문이 길어 토큰 여유).
- `generate.txt` 프롬프트가 passage/group_id 출력 스키마와 지문 블록 규칙을 명시.

## 2026-04-26 (latest)

### Added
- 문제별 **제외(`excluded`) 토글** — 문제 카드의 [🚫 제외] 버튼.
- 제외된 문제는:
  - 채점 차단 (`POST /api/quiz/answer` 가 409 반환)
  - 다음 출제(`/api/quiz/generate`) 시 LLM 프롬프트의 `EXCLUDED_QUESTIONS` 블록에 들어가 회피 출제
  - 약점 노트(`/api/wrong/list`) · 약점 강화(`/api/wrong/practice`) 에서 모두 숨김
- API: `POST /api/quiz/exclude/{question_id}` (body: `{user_id, excluded}`).
- `questions.excluded BOOLEAN` 컬럼 + 인덱스.

## 2026-04-26 (newer)

### Changed (UI)
- 테스트 타입 입력을 **콤보박스** 로 전환. 과거 타입 + 문서 수 + 최근 업로드 일자가 한 줄에 표시되며, "＋ 새 테스트 타입 만들기…" 옵션으로 신규 생성.
- 강의노트 또한 **콤보박스** 로 전환. 파일명 + 회차 수 + 최근 업로드 일자 표시. 새 노트 업로드는 `<details>` 로 접어 둠.
- 출제 옵션 UI 컴팩트화 — 문항/객관식/주관식을 3열 그리드, 추가 지시는 `<details>` 안으로.
- **스타일 칩 다중 선택**: 기본 7종(기출, 암기, 계산, 사례형, 약술형, 빈칸채우기, 비교형) + 사용자 정의 스타일(Enter 로 추가, × 클릭으로 제거, localStorage 누적).

## 2026-04-26 (newest)

### Breaking / Redesign
- **사용자 ID + Test Type 분리.** 이전까지 `user_id` 한 필드가 "ID"·"카테고리"를 겸했지만 이제는
  - **사용자 ID** = 로그인 식별자 (헤더)
  - **Test Type** = 한 사용자 안의 분류 (좌측 사이드바)
  로 분리되었습니다. 강의노트 업로드 전 강제 흐름: **로그인 → 테스트 타입 선택 → 업로드**.
- 기존 데이터의 `documents.test_type` / `quiz_sessions.test_type` 결측은 마이그레이션이 자동으로 `'기본'` 으로 채웁니다 (무손실).

### Added
- 좌측 트리: `📂 test_type → 📄 document → 🎯 session(점수)` 계층 표시. 클릭으로 활성화·이력 조회.
- 우측 상단 **대시보드**: test_type 별 문서/회차/문제/정답률/약점 토픽/최근 활동.
- API: `GET /api/quiz/test-types`, `GET /api/quiz/tree`, `GET /api/quiz/dashboard`.
- 단계별 게이트(① 로그인 ② Test Type ③ 업로드/출제) UI.

### Changed
- `POST /api/upload` 이 `test_type` 필드를 **필수**로 요구.
- 중복 업로드 유니크 인덱스 키가 `(owner_id, filename/hash)` → `(owner_id, test_type, filename/hash)` 로 변경 (다른 test_type 에서는 같은 파일 OK).

## 2026-04-26 (latest)

### Added
- Claude **OAuth 토큰 + Auto Login** 지원. `[Claude 로그인]` 버튼이 로컬 `~/.claude/.credentials.json` 등에서 자격을 자동 탐색·채택. 실패 시 모달에서 `sk-ant-api03-…`(API Key) 또는 `sk-ant-oat01-…`(Pro/Max OAuth) 직접 입력 가능.
- `POST /api/auth/claude/login` (자동 로그인) 엔드포인트 신설.
- `/api/auth/claude` GET 응답에 `token_kind` (api-key|oauth), `plan_aware` 필드 추가 — 사용자 플랜 토큰 사용 여부 표시.

### Changed
- 헤더 라벨이 토큰 종류에 따라 `Claude 연결됨 (OAuth · 플랜 토큰)` / `(API Key)` 로 구분 표시.

## 2026-04-26 (earlier)

### Added
- 강의노트 중복 업로드 방지 — 같은 카테고리 내 동일 파일명 또는 동일 내용(SHA-256) 이면 409 + 기존 `document_id` 안내.
- `documents.content_hash` 컬럼 + 부분 유니크 인덱스 2종.

## 2026-04-26 (later)

### Changed
- "ID" 라벨을 **Test Type (카테고리)** 로 변경 (사용자가 카테고리 단위로 풀이 이력을 분리·재진입).
- 헤더 입력 필드를 `<datalist>` 자동완성으로 전환 — 기존 카테고리 핸들을 다시 선택 가능.

### Added
- `GET /api/users` — 지금까지 등록된 카테고리 핸들 목록(생성일자 기준 최신 200개) 자동완성 후보.
- `localStorage["quiz_test_types"]` 에 최근 사용 카테고리 50개 누적 저장 (서버와 합쳐 dedup).

## 2026-04-26

### Added
- Claude 인증 모달 (헤더 우측 상단) + `/api/auth/claude` (POST/GET/DELETE). 키는 서버 메모리에만 보관.
- 회차(`quiz_sessions`) 자동 저장 + 풀이 이력 조회 UI: `/api/quiz/sessions`, `/api/quiz/sessions/{sid}`, `DELETE /api/quiz/sessions/{sid}`.
- 지연 해설 엔드포인트 `/api/quiz/explain/{qid}` — 답안 제출 시 즉시 채점하되 정답·근거·레퍼런스는 사용자가 명시 요청 시에만 노출.
- 멀티 에이전트 룰북 (`CLAUDE.md`) + 3개 서브에이전트(`.claude/agents/{frontend,backend,document}-agent.md`) + 강제 호출 훅(`.claude/hooks/`) + 주기 트리거 3종 (CronCreate).

### Changed
- 업로드 한도 20MB → **50MB** (`.env`, `.env.example`, `app/config.py`).
- `POST /api/quiz/answer` 응답 변경: 정답·해설을 노출하지 않고 `{submitted, question_id, attempt_id}` 만 반환. 해설은 `/api/quiz/explain/{qid}` 로.
- LLM 토큰 소스: `.env.ANTHROPIC_API_KEY` → `app.services.auth.get_token()` (메모리 우선, env 폴백).
- 기존 `AnswerResponse` 스키마 → `SubmittedResponse` + `ExplainResponse` 로 분리.
