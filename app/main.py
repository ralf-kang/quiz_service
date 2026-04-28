import logging
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .routers import admin, auth, quiz, upload, wrong
from .services import auth as auth_svc, auto_gen as auto_gen_svc, llm as llm_svc

logger = logging.getLogger(__name__)


_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def _is_loopback(request: Request) -> bool:
    # client.host 가 루프백이고 Host 헤더의 host 부분도 루프백이면 디버깅 모드로 본다
    client_host = (request.client.host if request.client else "") or ""
    raw_host = request.headers.get("host", "")
    host = raw_host.split(":", 1)[0]
    return client_host in _LOOPBACK_HOSTS and host in _LOOPBACK_HOSTS


WEB_DIR = Path(__file__).resolve().parent.parent / "web"

app = FastAPI(title="Quiz / 모의고사 웹서비스", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def loopback_flag_middleware(request: Request, call_next):
    llm_svc.set_loopback(_is_loopback(request))
    try:
        return await call_next(request)
    finally:
        llm_svc.set_loopback(False)

app.include_router(auth.router)
app.include_router(upload.router)
app.include_router(quiz.router)
app.include_router(wrong.router)
app.include_router(admin.router)


# in-process 스케줄러 핸들 (startup/shutdown 사이에서 공유)
_scheduler = None


@app.on_event("startup")
def _bootstrap_credentials() -> None:
    auth_svc.try_load_claude_cli_credentials()


@app.on_event("startup")
def _start_auto_gen_scheduler() -> None:
    """자동 출제 스케줄러 가동.

    AUTO_GEN_ENABLED=false 면 스킵. 첫 실행은 앱 기동 5분 뒤로 미뤄,
    부팅 직후 부하/마이그레이션과 겹치지 않게 한다.
    """
    global _scheduler
    if not settings.AUTO_GEN_ENABLED:
        logger.info("auto_gen: disabled by AUTO_GEN_ENABLED=false")
        return
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        logger.warning("auto_gen: APScheduler 미설치 — 스케줄러 비활성")
        return

    def _job() -> None:
        try:
            result = auto_gen_svc.run_auto_round(
                limit=settings.AUTO_GEN_LIMIT_PER_ROUND,
                n_questions=settings.AUTO_GEN_QUESTIONS,
            )
            logger.info("auto_gen: scheduled round result=%s", result)
        except Exception as e:  # noqa: BLE001
            logger.exception("auto_gen: scheduled round crashed: %s", e)

    sched = BackgroundScheduler(timezone="UTC")
    first_run = datetime.utcnow() + timedelta(minutes=5)
    sched.add_job(
        _job,
        trigger="interval",
        seconds=settings.AUTO_GEN_INTERVAL_SEC,
        next_run_time=first_run,
        id="auto_gen_round",
        max_instances=1,
        coalesce=True,
    )
    sched.start()
    _scheduler = sched
    logger.info(
        "auto_gen: scheduler started interval=%ds first_run=%s",
        settings.AUTO_GEN_INTERVAL_SEC, first_run.isoformat(),
    )


@app.on_event("shutdown")
def _stop_auto_gen_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=False)
            logger.info("auto_gen: scheduler stopped")
        except Exception as e:  # noqa: BLE001
            logger.warning("auto_gen: scheduler shutdown error: %s", e)
        _scheduler = None


@app.get("/health")
def health(request: Request):
    loopback = _is_loopback(request)
    import shutil as _shutil
    cli_present = _shutil.which("claude") is not None
    # 루프백 + CLI 가 있으면 토큰이 없어도 LLM 사용 가능 (CLI 패스스루)
    llm_ok = auth_svc.is_configured() or (loopback and cli_present)
    return {
        "ok": True,
        "llm_configured": llm_ok,
        "loopback_debug": loopback,
        "claude_cli_present": cli_present,
    }


# Serve the static frontend at /
if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

    @app.get("/")
    def root():
        idx = WEB_DIR / "index.html"
        if idx.exists():
            return FileResponse(str(idx))
        return {"ok": True, "msg": "frontend not present"}
