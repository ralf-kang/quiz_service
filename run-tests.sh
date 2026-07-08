#!/usr/bin/env bash
# CI/CD 게이트용 테스트 러너 — quiz_service
#
# 계약 (kang-util cicd-gated.sh 와 공유):
#   - 실행 후 레포 루트에 pytest-json-report 형식 .cicd-test-report.json 을 남긴다.
#   - exit 0  = 테스트가 실제로 "실행"되어 리포트가 생성됨 (통과/실패 여부와 무관).
#   - exit !=0 = 인프라 문제(docker 미기동, 빌드 실패 등)로 테스트 자체를 실행하지 못함.
#
# 자격증명은 다루지 않는다. app 서비스 실행에 CLAUDE_CODE_OAUTH_TOKEN 등이 필요하더라도
# 이 스크립트는 별도로 주입하지 않고 compose/.env 에 이미 설정된 값을 그대로 사용한다.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

REPORT_HOST="$REPO_ROOT/.cicd-test-report.json"
REPORT_CONTAINER="/app/.cicd-test-report.json"

rm -f "$REPORT_HOST"

echo "[run-tests] db 컨테이너 기동/대기..."
docker compose up -d db
# compose v2.17+ 는 --wait 지원. 미지원 버전은 healthcheck 상태를 직접 폴링.
if ! docker compose up -d --wait db 2>/dev/null; then
  for _ in $(seq 1 30); do
    status="$(docker compose ps db --format '{{.Health}}' 2>/dev/null || true)"
    if [ "$status" = "healthy" ]; then
      break
    fi
    sleep 2
  done
fi

echo "[run-tests] app 컨테이너에서 pytest 실행..."
docker compose run --rm \
  -e AUTO_GEN_ENABLED=false \
  -v "$REPO_ROOT":/app/hostroot \
  app sh -c "pip install -q -r requirements-test.txt && \
    pytest -q --json-report --json-report-file=${REPORT_CONTAINER} tests/ ; \
    cp -f ${REPORT_CONTAINER} /app/hostroot/.cicd-test-report.json 2>/dev/null ; \
    true"

if [ ! -f "$REPORT_HOST" ]; then
  echo "[run-tests] ERROR: .cicd-test-report.json 이 생성되지 않았음 (인프라/빌드 실패 가능성)" >&2
  exit 1
fi

echo "[run-tests] 리포트 생성 완료: $REPORT_HOST"
exit 0
