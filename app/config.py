from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str = "postgresql+psycopg2://webservice_user:webservice_pass@localhost:5432/webservice_db"
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-sonnet-4-6"
    MAX_UPLOAD_MB: int = 50
    CHUNK_SIZE: int = 1000

    # 자동 문제 생성 스케줄러 (in-process APScheduler)
    AUTO_GEN_ENABLED: bool = True
    AUTO_GEN_INTERVAL_SEC: int = 600          # 10분
    AUTO_GEN_LIMIT_PER_ROUND: int = 5         # 한 cycle 처리 최대 대상 수
    AUTO_GEN_QUESTIONS: int = 3               # 대상별 생성 문항 수


settings = Settings()
