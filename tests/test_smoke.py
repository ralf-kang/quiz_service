"""CI/CD 게이트용 최소 smoke 테스트.

critical 마커가 붙은 테스트의 실패는 게이트가 크리티컬 실패로 집계한다
(자세한 계약은 kang-util `infra/agent/CICD-GATED.md` 참고).
"""

import pytest


@pytest.mark.critical
def test_root_ok(client):
    resp = client.get("/")
    assert resp.status_code == 200


@pytest.mark.critical
def test_version(client):
    resp = client.get("/api/version")
    assert resp.status_code == 200
    data = resp.json()
    assert "version" in data


def test_health_or_docs(client):
    # FastAPI 기본 제공 Swagger UI — 비크리티컬 예시
    resp = client.get("/docs")
    assert resp.status_code == 200


def test_openapi_schema_available(client):
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    assert "paths" in resp.json()


def test_version_payload_has_updated_key(client):
    resp = client.get("/api/version")
    data = resp.json()
    assert "updated" in data
