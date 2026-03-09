from __future__ import annotations

import os

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    project_name: str = "ShortLinks API"

    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/shortlinks",
    )
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    secret_key: str = os.getenv("SECRET_KEY", "change-me-in-production")
    jwt_algorithm: str = os.getenv("JWT_ALGORITHM", "HS256")
    access_token_expire_minutes: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 60 * 24))

    shortcode_length: int = int(os.getenv("SHORTCODE_LENGTH", 7))
    base_url: str = os.getenv("BASE_URL", "http://localhost:8000")

    redirect_cache_ttl_seconds: int = int(os.getenv("REDIRECT_CACHE_TTL_SECONDS", 3600))
    stats_cache_ttl_seconds: int = int(os.getenv("STATS_CACHE_TTL_SECONDS", 60))
    popular_cache_ttl_seconds: int = int(os.getenv("POPULAR_CACHE_TTL_SECONDS", 120))

    cleanup_interval_seconds: int = int(os.getenv("CLEANUP_INTERVAL_SECONDS", 60))
    unused_days_threshold: int = int(os.getenv("UNUSED_DAYS_THRESHOLD", 30))

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        url = str(value).strip()
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://") :]
        if url.startswith("postgresql://") and not url.startswith("postgresql+asyncpg://"):
            url = "postgresql+asyncpg://" + url[len("postgresql://") :]
        return url


settings = Settings()
