from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    project_name: str = "ShortLinks API"

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/shortlinks"
    redis_url: str = "redis://localhost:6379/0"

    secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24

    shortcode_length: int = 7
    base_url: str = "http://localhost:8000"

    redirect_cache_ttl_seconds: int = 3600
    stats_cache_ttl_seconds: int = 60
    popular_cache_ttl_seconds: int = 120

    cleanup_interval_seconds: int = 60
    unused_days_threshold: int = 30

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
