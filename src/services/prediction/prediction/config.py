"""Environment-backed configuration for the Prediction Service."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class PredictionSettings(BaseSettings):
    """Settings read from ``PREDICTION_*`` plus the shared infra variables."""

    model_config = SettingsConfigDict(env_prefix="PREDICTION_", extra="ignore")

    # Shared infra connection strings use their conventional unprefixed names.
    database_url: str = Field(validation_alias="DATABASE_URL")
    rabbitmq_url: str = Field(validation_alias="RABBITMQ_URL")
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")

    events_queue: str = Field(default="prediction.events")

    # Event-time context aggregation. Distinct events for one asset within a tumbling window of this
    # size form one versioned context (ADR-001). Article/event count never triggers a prediction.
    context_window_minutes: int = Field(default=60, gt=0)
    # Grace after a window's end before it is eligible to close, allowing slightly late events in.
    close_grace_minutes: int = Field(default=5, ge=0)
    close_interval_seconds: int = Field(default=60, gt=0)

    # Decision policy (graph-only). A firing set with net directional ratio below the deadband is
    # reported as NEUTRAL; magnitude buckets split the agreeing-edge average expert weight.
    decision_deadband: float = Field(default=0.15, ge=0.0, lt=1.0)
    magnitude_small_max: float = Field(default=0.40, gt=0.0, lt=1.0)
    magnitude_medium_max: float = Field(default=0.70, gt=0.0, le=1.0)

    # Scope-B price gate. A RESOLUTION-driven DOWN is only emitted when the asset's latest close is
    # elevated versus the mean of the prior sessions by at least this fraction; otherwise there is
    # no risk premium to unwind. Recent closes are read from the Market Data service.
    market_data_base_url: str = Field(default="http://feed-market-data:8000")
    price_lookback_sessions: int = Field(default=10, gt=0)
    price_elevated_threshold_pct: float = Field(default=0.01, ge=0.0)
    market_data_timeout_seconds: float = Field(default=5.0, gt=0)

    db_pool_min_size: int = Field(default=1, ge=1)
    db_pool_max_size: int = Field(default=5, ge=1)
