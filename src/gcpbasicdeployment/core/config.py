"""Settings read from environment variables (and a git-ignored .env locally)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://user:password@localhost:5432/linkrot"
    port: int = 8000
    log_level: str = "INFO"
    check_timeout_seconds: float = 10.0
    max_concurrency: int = 10


@lru_cache
def get_settings() -> Settings:
    return Settings()
