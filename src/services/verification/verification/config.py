"""Environment-backed configuration for the Verification Service."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class VerificationSettings(BaseSettings):
    """Settings read from ``VERIFICATION_*`` plus the shared infra variables."""

    model_config = SettingsConfigDict(env_prefix="VERIFICATION_", extra="ignore")

    database_url: str = Field(validation_alias="DATABASE_URL")
    rabbitmq_url: str = Field(validation_alias="RABBITMQ_URL")
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")

    predictions_queue: str = Field(default="verification.predictions")
    prices_queue: str = Field(default="verification.prices")

    # Scoring policy (verification functional document sec 4).
    deadband: float = Field(default=0.003, ge=0.0, lt=1.0)
    magnitude_medium_min: float = Field(default=0.01, gt=0.0, lt=1.0)
    magnitude_large_min: float = Field(default=0.03, gt=0.0, le=1.0)

    outbox_interval_seconds: int = Field(default=30, gt=0)

    db_pool_min_size: int = Field(default=1, ge=1)
    db_pool_max_size: int = Field(default=5, ge=1)
