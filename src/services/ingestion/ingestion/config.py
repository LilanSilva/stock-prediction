"""Ingestion service configuration (environment-backed)."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class IngestionSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    # Shared infrastructure connection strings (from infra/.env).
    database_url: str = "postgresql://feed_user:local_dev_pw@localhost:5432/feed"
    rabbitmq_url: str = "amqp://feed_user:local_dev_pw@localhost:5672/"

    # Service behavior.
    poll_interval_seconds: int = Field(default=3600, gt=0)
    body_fetch_enabled: bool = True
    body_fetch_overall_timeout_seconds: float = Field(default=20.0, gt=0)
    body_fetch_concurrency: int = Field(default=10, ge=1)
    body_max_chars: int = Field(default=2000, gt=0)
    feed_fetch_timeout_seconds: float = Field(default=30.0, gt=0)
    db_pool_min_size: int = Field(default=1, ge=1)
    db_pool_max_size: int = Field(default=5, ge=1)
    circuit_failure_threshold: int = Field(default=5, gt=0)
    circuit_reset_timeout_seconds: float = Field(default=60.0, gt=0)

    # Time-based retention. Article rows older than the window can never be re-ingested (feeds only
    # surface recent items), so they are safe to delete; delivered outbox rows are prunable too.
    retention_enabled: bool = True
    article_retention_days: int = Field(default=30, ge=1)
    outbox_retention_days: int = Field(default=7, ge=1)
    retention_interval_seconds: int = Field(default=86400, gt=0)

    # FreeNewsApi.io (keyed global news search; POC-8). Replaced GDELT, which returned 0 records
    # under an undocumented HTTP 429 IP rate-limit. Failures trip this source's own circuit breaker.
    # The API key is a secret injected via FREENEWSAPI_KEY (never committed); an empty key disables
    # the source so the four RSS feeds still run.
    freenewsapi_key: str = Field(default="", validation_alias="FREENEWSAPI_KEY")
    freenewsapi_base_url: str = "https://api.freenewsapi.io/v1"
    freenewsapi_language: str = "en"
    freenewsapi_page_size: int = Field(
        default=5, gt=0, le=100, validation_alias="FREENEWSAPI_PAGE_SIZE"
    )

    log_level: str = "INFO"
