"""Anthropic Claude wrapper. Graceful when API key absent.

루프백 디버깅 모드에서는 토큰이 없을 때 로컬 `claude` CLI 를 패스스루로 호출해
사용자의 Claude Code 세션을 그대로 재사용한다.
"""
import json
import os
import shutil
import subprocess
from typing import Optional

from ..config import settings


# 라우터에서 `Host:` 헤더를 보고 루프백 여부를 판정해 이 플래그를 켠다.
_loopback_request: bool = False


class LLMUnavailableError(RuntimeError):
    """Raised when the Anthropic API key is not configured."""


def set_loopback(flag: bool) -> None:
    """현재 처리 중인 요청이 루프백에서 들어왔는지 표시 (요청별 컨텍스트 변수 대용 — 단일 프로세스 단일 호출 가정)."""
    global _loopback_request
    _loopback_request = bool(flag)


def _claude_cli_passthrough(system: str, user: str, max_tokens: int, oauth_token: str) -> Optional[str]:
    """`claude -p` 비대화형 모드 호출. 실패 시 None.

    oauth_token: CLAUDE_CODE_OAUTH_TOKEN 값 — env 에 명시적으로 주입해 컨테이너·스케줄러
    양 경로 모두 인증이 보장되게 한다. ANTHROPIC_API_KEY 는 충돌 방지를 위해 unset.
    """
    cli = shutil.which("claude")
    if not cli:
        return None

    prompt = f"{system}\n\n---\n\n{user}"
    env = os.environ.copy()
    env["CLAUDE_CODE_OAUTH_TOKEN"] = oauth_token
    env.pop("ANTHROPIC_API_KEY", None)
    env.setdefault("CI", "1")  # 일부 CLI 가 비대화형 출력으로 전환
    try:
        proc = subprocess.run(
            [cli, "-p", prompt, "--model", settings.ANTHROPIC_MODEL,
             "--output-format", "text"],
            input="",
            capture_output=True,
            text=True,
            timeout=180,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return None
    except Exception:
        return None

    if proc.returncode != 0:
        # 일부 옵션이 안 맞을 수 있으니 최소 옵션으로 재시도
        try:
            proc = subprocess.run(
                [cli, "-p", prompt],
                input="",
                capture_output=True,
                text=True,
                timeout=180,
                env=env,
            )
        except Exception:
            return None
        if proc.returncode != 0:
            return None

    out = (proc.stdout or "").strip()
    return out or None


def _client():
    from . import auth

    token = auth.get_token()
    if not token:
        raise LLMUnavailableError(
            "Claude 인증이 필요합니다. 화면 우측 상단의 'Claude 로그인' 버튼으로 키를 등록하세요."
        )
    import anthropic
    return anthropic.Anthropic(api_key=token)


def call_claude(system: str, user: str, max_tokens: int = 4096, temperature: float = 0.4) -> str:
    """LLM 호출 — 토큰 종류에 따라 경로를 선택한다.

    * oauth (sk-ant-oat 또는 CLAUDE_CODE_OAUTH_TOKEN 환경변수):
        → `_claude_cli_passthrough` 사용 (구독 인증, 루프백 여부 무관).
        auto_gen 스케줄러 포함 모든 경로에서 동작.
    * api-key (sk-ant-api03):
        → Anthropic Python SDK (_client) 사용.
    * 토큰 없음:
        → LLMUnavailableError.

    _loopback_request / set_loopback 플래그는 하위 호환성을 위해 유지되나
    새 라우팅에서는 참조하지 않는다.
    """
    from . import auth

    token = auth.get_token()
    kind = auth._classify(token) if token else None

    # oauth 토큰(구독) → CLI 패스스루 (루프백·스케줄러 모두 동일 경로)
    if kind == "oauth" or (token and os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip()):
        effective_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip() or token
        out = _claude_cli_passthrough(system, user, max_tokens, oauth_token=effective_token)
        if out is not None:
            return out
        raise LLMUnavailableError(
            "claude CLI 패스스루 호출에 실패했습니다. 컨테이너에 `claude` 가 PATH 에 있고 "
            "CLAUDE_CODE_OAUTH_TOKEN 이 올바르게 주입되었는지 확인하세요."
        )

    # api-key → SDK 경로
    if kind in ("api-key", "unknown") and token:
        import anthropic

        client = _client()
        try:
            msg = client.messages.create(
                model=settings.ANTHROPIC_MODEL,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
        except anthropic.AuthenticationError as e:
            raise LLMUnavailableError(
                "Claude 인증 실패: API 키가 올바르지 않거나 권한이 없습니다. 우측 상단 [Claude 로그인] 으로 다시 등록하세요."
            ) from e
        except anthropic.RateLimitError as e:
            raise LLMUnavailableError(
                "Claude API 호출 한도에 도달했습니다. 잠시 후 다시 시도하세요."
            ) from e
        except anthropic.APIConnectionError as e:
            raise LLMUnavailableError(
                "Claude API 서버에 연결할 수 없습니다. 네트워크를 확인하세요."
            ) from e
        except anthropic.APIStatusError as e:
            raise LLMUnavailableError(
                f"Claude API 오류 ({e.status_code}): {getattr(e, 'message', str(e))}"
            ) from e

        parts = []
        for block in msg.content:
            if getattr(block, "type", None) == "text":
                parts.append(block.text)
        return "".join(parts).strip()

    # 토큰 없음
    raise LLMUnavailableError(
        "Claude 인증이 필요합니다. 화면 우측 상단의 'Claude 로그인' 버튼으로 키를 등록하세요."
    )


def parse_json_block(text: str) -> Optional[dict | list]:
    """Pull the first JSON object/array out of a model response."""
    if not text:
        return None

    # Strip ```json fences
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()

    try:
        return json.loads(cleaned)
    except Exception:
        pass

    # Fallback: scan for the first balanced { ... } or [ ... ]
    for opener, closer in (("{", "}"), ("[", "]")):
        start = cleaned.find(opener)
        if start < 0:
            continue
        depth = 0
        for i in range(start, len(cleaned)):
            ch = cleaned[i]
            if ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    candidate = cleaned[start : i + 1]
                    try:
                        return json.loads(candidate)
                    except Exception:
                        break
    return None
