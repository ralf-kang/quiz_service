"""관리/디버깅 전용 엔드포인트 (loopback 호출만 허용)."""
from fastapi import APIRouter, HTTPException, Request

from ..config import settings
from ..services import auto_gen


router = APIRouter(prefix="/api/admin", tags=["admin"])


_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def _require_loopback(request: Request) -> None:
    client_host = (request.client.host if request.client else "") or ""
    raw_host = request.headers.get("host", "")
    host = raw_host.split(":", 1)[0]
    if not (client_host in _LOOPBACK_HOSTS and host in _LOOPBACK_HOSTS):
        raise HTTPException(403, "loopback only")


@router.post("/auto-gen/run")
def auto_gen_run(request: Request):
    """수동 트리거: 즉시 1회 round 를 실행하고 결과 반환.

    스케줄러를 기다리지 않고 디버깅하기 위한 엔드포인트. loopback 전용.
    """
    _require_loopback(request)
    result = auto_gen.run_auto_round(
        limit=settings.AUTO_GEN_LIMIT_PER_ROUND,
        n_questions=settings.AUTO_GEN_QUESTIONS,
    )
    return {
        "interval_sec": settings.AUTO_GEN_INTERVAL_SEC,
        "enabled": settings.AUTO_GEN_ENABLED,
        **result,
    }
