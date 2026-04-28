#!/usr/bin/env bash
# PostToolUse hook (matcher: Edit|Write) — routes follow-up checks based on file path.
# stdin is a JSON payload from Claude Code; we extract tool_input.file_path with python (no jq dependency).
set -euo pipefail

payload="$(cat || true)"

path="$(python3 - <<'PY' "$payload"
import json, sys
try:
    data = json.loads(sys.argv[1])
except Exception:
    print("")
    sys.exit(0)
ti = (data.get("tool_input") or {})
print(ti.get("file_path") or "")
PY
)"

[ -z "$path" ] && exit 0

emit() { echo "[hook] $1 → $2 강제 호출 권고. 후속 검증/요약은 해당 에이전트로 위임하세요."; }

case "$path" in
    */web/*)
        emit "web/ 변경" "frontend-agent"
        ;;
    */app/routers/*|*/app/services/*|*/app/models.py|*/app/main.py|*/app/db.py|*/app/config.py|*/app/schemas.py|*/app/deps.py|*/init_db.sql|*/requirements.txt)
        emit "백엔드 코어 변경 ($path)" "backend-agent"
        ;;
    *.md|*/app/prompts/*.txt|*/CHANGELOG*)
        emit "문서/프롬프트 변경 ($path)" "document-agent"
        ;;
    */.env|*/.env.*)
        emit ".env 변경 — 비밀값 노출 금지. 동기화" "backend-agent"
        ;;
esac
