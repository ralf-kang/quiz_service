from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..services import auth as auth_svc


router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    api_key: str


@router.get("/claude")
def status():
    """현재 인증 상태 + 자동 로그인 가이드. 토큰 본문은 절대 노출하지 않는다."""
    s = auth_svc.status()
    s["hint"] = auth_svc.cli_setup_hint()
    return s


@router.post("/claude/login")
def auto_login():
    """[Claude 로그인] 버튼 동작.

    로컬 PC 에 Claude Code CLI 자격이 있으면 자동 채택.
    실패하면 `configured: false` + hint 반환 — 프론트가 토큰 붙여넣기 모달 표시.
    """
    s = auth_svc.try_auto_login()
    if not s.get("configured"):
        s["hint"] = auth_svc.cli_setup_hint()
    return s


@router.post("/claude")
def login_with_token(req: LoginRequest):
    """API Key 또는 OAuth Token 직접 등록 (모달의 [등록] 버튼).

    `sk-ant-api03-...`  → API Key
    `sk-ant-oat01-...`  → OAuth Token (Pro/Max 플랜 한도 사용)
    """
    try:
        auth_svc.set_token(req.api_key, source="oauth-paste"
                           if req.api_key.strip().startswith("sk-ant-oat") else "user-input")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return auth_svc.status()


@router.delete("/claude")
def logout():
    """메모리에서 토큰 제거."""
    auth_svc.clear_token()
    return auth_svc.status()
