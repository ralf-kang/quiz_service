"""Server-memory Anthropic credential store.

지원되는 두 가지 자격 형식:

  • API Key       (`sk-ant-api03-...`)  — Anthropic Console 에서 발급
  • OAuth Token   (`sk-ant-oat01-...`)  — Claude Code CLI 의 `claude setup-token`
                                          또는 Pro/Max 플랜 OAuth 흐름의 결과

두 형식 모두 anthropic SDK 의 `x-api-key` 헤더로 동일하게 동작한다.
OAuth 토큰을 사용하면 사용자의 Pro/Max 플랜 한도가 적용된다.

키는 디스크/DB 에 저장하지 않고, 서버 재기동 시 소실된다.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Optional


_token: Optional[str] = None
_source: Optional[str] = None       # "user-input" | "env" | "claude-cli" | "oauth-paste" | None
_token_kind: Optional[str] = None   # "api-key" | "oauth" | "unknown"


def _classify(token: str) -> str:
    """토큰 prefix 로 종류를 추정. API 호출은 두 형식 모두 동일하게 동작."""
    t = token.strip()
    if t.startswith("sk-ant-oat"):
        return "oauth"
    if t.startswith("sk-ant-api"):
        return "api-key"
    return "unknown"


def set_token(token: str, source: str = "user-input") -> None:
    global _token, _source, _token_kind
    t = (token or "").strip()
    if not t:
        raise ValueError("토큰이 비어 있습니다.")
    _token = t
    _source = source
    _token_kind = _classify(t)


def clear_token() -> None:
    global _token, _source, _token_kind
    _token = None
    _source = None
    _token_kind = None


def get_token() -> Optional[str]:
    if _token:
        return _token
    # CLAUDE_CODE_OAUTH_TOKEN (구독 토큰) — SDK가 아닌 CLI 패스스루 경로에서 사용됨
    oauth_env = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
    if oauth_env:
        return oauth_env
    from ..config import settings

    if settings.ANTHROPIC_API_KEY:
        return settings.ANTHROPIC_API_KEY
    return None


def is_configured() -> bool:
    return get_token() is not None


def status() -> dict:
    src = _source
    kind = _token_kind
    if not _token:
        oauth_env = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
        if oauth_env:
            src = "env"
            kind = _classify(oauth_env)
        else:
            from ..config import settings

            if settings.ANTHROPIC_API_KEY:
                src = "env"
                kind = _classify(settings.ANTHROPIC_API_KEY)
    return {
        "configured": is_configured(),
        "source": src,            # 어떻게 등록되었는지 (절대 토큰 자체는 노출 X)
        "token_kind": kind,       # api-key | oauth | unknown
        "plan_aware": kind == "oauth",  # 사용자 플랜 한도가 적용되는지 힌트
    }


# --------------------------------------------------------------------------- #
# Auto-login: 서버 기동 또는 사용자 명시 요청 시 로컬 PC 의 Claude CLI 자격 재사용 시도
# --------------------------------------------------------------------------- #

def _read_claude_cli_token() -> tuple[Optional[str], Optional[str]]:
    """Claude Code CLI 가 보관한 자격을 best-effort 로 읽어 (token, source) 반환.

    탐색 위치(존재 우선순위 순):
      1) $ANTHROPIC_API_KEY 환경변수
      2) ~/.claude/.credentials.json 또는 ~/.claude/credentials.json
         의 access_token / api_key 필드
      3) macOS Keychain (보안상 직접 추출 X — 사용자에게 모달로 안내)
    """
    env_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if env_key:
        return env_key, "env"

    candidates = [
        Path.home() / ".claude" / ".credentials.json",
        Path.home() / ".claude" / "credentials.json",
        Path.home() / ".config" / "claude" / "credentials.json",
    ]
    for p in candidates:
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        # 알려진 후보 키 이름들
        for k in (
            "access_token", "accessToken",
            "api_key", "apiKey",
            "token", "key",
        ):
            v = data.get(k) if isinstance(data, dict) else None
            if isinstance(v, str) and v.startswith("sk-ant-"):
                return v.strip(), "claude-cli"
        # 중첩 구조 대응 (claude.code, profiles 등)
        for parent_key in ("claudeAiOauth", "claude.code", "profiles", "default"):
            child = data.get(parent_key) if isinstance(data, dict) else None
            if isinstance(child, dict):
                for k in ("accessToken", "access_token", "apiKey", "api_key"):
                    v = child.get(k)
                    if isinstance(v, str) and v.startswith("sk-ant-"):
                        return v.strip(), "claude-cli"
    return None, None


def try_auto_login() -> dict:
    """사용자가 [Claude 로그인] 버튼을 눌렀을 때 호출.

    이미 토큰이 있으면 그대로 status 반환.
    없으면 로컬 PC 의 Claude CLI 자격을 찾아 자동 등록 시도.
    """
    if _token:
        return status()

    tok, src = _read_claude_cli_token()
    if tok:
        set_token(tok, source=src or "claude-cli")
    return status()


def try_load_claude_cli_credentials() -> bool:
    """서버 startup 훅. 에러 무시."""
    if _token:
        return True
    try:
        s = try_auto_login()
        return bool(s.get("configured"))
    except Exception:
        return False


def cli_setup_hint() -> dict:
    """프론트에 'claude setup-token' 명령 가이드를 줄 때 사용."""
    return {
        "claude_cli_present": shutil.which("claude") is not None,
        "instructions": [
            "터미널에서 `claude setup-token` 실행 (Claude Pro/Max 사용자) 또는 "
            "https://console.anthropic.com/settings/keys 에서 API 키 발급",
            "출력된 토큰(`sk-ant-oat01-...` 또는 `sk-ant-api03-...`) 을 모달에 붙여넣기",
        ],
    }
