"""Environment-backed configuration for the Notification Service."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class NotificationSettings(BaseSettings):
    """Settings read from ``NOTIFICATION_*`` plus the shared infra variables."""

    model_config = SettingsConfigDict(env_prefix="NOTIFICATION_", extra="ignore")

    rabbitmq_url: str = Field(validation_alias="RABBITMQ_URL")
    database_url: str = Field(default="", validation_alias="DATABASE_URL")
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")

    predictions_queue: str = Field(default="notification.predictions")

    min_confidence: float = Field(default=0.6, ge=0.0, le=1.0)
    channel_timeout_seconds: float = Field(default=30.0, gt=0)
    recipients_dir: str = Field(default="config/recipients")

    # Brevo (email channel) — empty disables the channel.
    brevo_api_key: str = Field(default="", validation_alias="BREVO_API_KEY")
    brevo_sender_email: str = Field(default="", validation_alias="BREVO_SENDER_EMAIL")
    brevo_sender_name: str = Field(default="Feed Analyzer", validation_alias="BREVO_SENDER_NAME")

    # Meta Cloud API (WhatsApp channel) — empty disables the channel.
    meta_access_token: str = Field(default="", validation_alias="META_ACCESS_TOKEN")
    meta_phone_number_id: str = Field(default="", validation_alias="META_PHONE_NUMBER_ID")
    meta_api_version: str = Field(default="v18.0", validation_alias="META_API_VERSION")
