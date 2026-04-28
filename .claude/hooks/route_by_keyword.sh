#!/usr/bin/env bash
# UserPromptSubmit hook — emits routing hints to the advisor based on user prompt keywords.
# Output goes to stdout and is injected into the model's context as a system reminder.
set -euo pipefail

raw="$(cat || true)"
prompt="$(printf '%s' "$raw" | tr '[:upper:]' '[:lower:]')"

hits=()

# Frontend keywords (Korean + English)
if printf '%s' "$prompt" | grep -qE "프론트|화면|버튼|레이아웃|색상|css|index\.html|app\.js|style\.css|콘솔|접근성|ui|ux"; then
    hits+=("frontend-agent")
fi

# Backend keywords
if printf '%s' "$prompt" | grep -qE "api|엔드포인트|라우터|스키마|마이그레이션|sqlalchemy|uvicorn|pydantic|채점 로직|업로드|pg_trgm|psql|fastapi|services/|routers/|models\.py|init_db"; then
    hits+=("backend-agent")
fi

# Document keywords
if printf '%s' "$prompt" | grep -qE "readme|문서|plan|프롬프트|스펙|주석|changelog|adr|가이드"; then
    hits+=("document-agent")
fi

if [ ${#hits[@]} -eq 0 ]; then
    exit 0
fi

# De-duplicate while preserving order
seen=" "
for a in "${hits[@]}"; do
    case "$seen" in *" $a "*) continue ;; esac
    echo "[hook] 키워드 매치 → $a 강제 호출 권고. CLAUDE.md 의 위임 표준 형식을 사용해 Agent 도구로 호출하세요."
    seen="$seen$a "
done
