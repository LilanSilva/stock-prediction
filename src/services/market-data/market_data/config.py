"""Market Data service configuration (environment-backed)."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MarketDataSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    # Shared infrastructure connection strings (from infra/.env).
    database_url: str = "postgresql://feed_user:local_dev_pw@localhost:5432/feed"
    rabbitmq_url: str = "amqp://feed_user:local_dev_pw@localhost:5672/"

    # Owned work queue (declared by infra/rabbitmq/definitions.json; the client only looks it up).
    price_requests_queue: str = "market-data.price-requests"

    # Provider: the Yahoo chart API serves every registry asset. Canonical asset IDs are resolved to
    # provider symbols via the shared asset registry; no provider symbol is configured here.
    # biquote.io was retired 2026-08-29; its adapter remains wired but no asset routes to it.
    biquote_base_url: str = "https://biquote.io/api"
    yahoo_base_url: str = "https://query1.finance.yahoo.com/v8/finance/chart"
    provider_timeout_seconds: float = Field(default=30.0, gt=0)
    # Calendar days of history fetched around a session so weekend/holiday gaps still yield a bar.
    fetch_window_days: int = Field(default=10, ge=2)

    # Settlement polling: how often to re-attempt requests whose settlement session has not yet
    # completed/published, and the bounded backoff for transient provider failures. Daily closes
    # settle once per day, so hourly re-drive is sufficient (a completed close is picked up within
    # the hour, which is irrelevant for next-trading-day scoring).
    settlement_poll_interval_seconds: int = Field(default=3600, gt=0)
    retry_backoff_base_seconds: float = Field(default=2.0, gt=0)
    retry_backoff_max_seconds: float = Field(default=8.0, gt=0)
    # A provider that has not published a bar this many days after the settlement session never
    # will; the request is abandoned instead of retried for the life of the deployment.
    abandon_after_settlement_days: int = Field(default=7, ge=1)

    db_pool_min_size: int = Field(default=1, ge=1)
    db_pool_max_size: int = Field(default=5, ge=1)

    log_level: str = "INFO"
