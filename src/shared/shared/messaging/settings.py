"""Messaging configuration."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class RabbitMQSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RABBITMQ_", extra="ignore")

    url: str = "amqp://feed_user:changeme@localhost:5672/"
    max_retries: int = 3
    prefetch_count: int = 10
    channel_pool_size: int = 10
