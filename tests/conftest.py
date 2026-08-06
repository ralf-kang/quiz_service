"""CI/CD 게이트용 최소 pytest 스캐폴드 — 공유 fixture.

자격증명(ANTHROPIC_API_KEY, CLAUDE_CODE_OAUTH_TOKEN 등)을 취급하지 않는다.
DB가 필요한 테스트는 compose db(db:5432) 또는 env DATABASE_URL을 사용해야 하며,
여기 정의된 smoke fixture 자체는 DB 접속을 요구하지 않는다.
"""

import os

# 스케줄러(APScheduler)가 테스트 중 백그라운드로 도는 것을 막는다.
# docker compose run -e AUTO_GEN_ENABLED=false 로 이미 지정되지만,
# 로컬에서 docker 없이 pytest만 돌릴 때도 안전하도록 setdefault 처리.
os.environ.setdefault("AUTO_GEN_ENABLED", "false")

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client():
    """FastAPI TestClient. startup/shutdown 이벤트를 포함해 실제 앱과 동일하게 기동."""
    with TestClient(app) as c:
        yield c
