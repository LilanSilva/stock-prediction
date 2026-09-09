"""Cleansing service configuration (environment-backed)."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CleansingSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CLEANSING_", extra="ignore")

    # Shared infrastructure connection strings (from infra/.env). These are read WITHOUT the
    # CLEANSING_ prefix so they match the shared DATABASE_URL / RABBITMQ_URL used everywhere.
    database_url: str = Field(
        default="postgresql://feed_user:local_dev_pw@localhost:5432/feed",
        validation_alias="DATABASE_URL",
    )
    rabbitmq_url: str = Field(
        default="amqp://feed_user:local_dev_pw@localhost:5672/",
        validation_alias="RABBITMQ_URL",
    )
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")

    # Consumed queue (owned by this service). Declared by infra/rabbitmq/definitions.json.
    articles_queue: str = "cleansing.articles"

    # Deduplication: SimHash near-duplicate detection within a rolling window.
    dedup_window_hours: int = Field(default=48, ge=1)
    simhash_max_distance: int = Field(default=3, ge=0, le=64)

    # Backends. "hashing"/"keyword" are deterministic, dependency-light defaults so the walking
    # skeleton runs and tests pass without downloading multi-GB models. "bge-m3"/"spacy" load the
    # real multilingual models lazily (requires the `ml` extra).
    embedding_backend: str = "hashing"
    nlp_backend: str = "keyword"
    embedding_dimension: int = Field(default=1024, gt=0)
    bge_model_name: str = "BAAI/bge-m3"

    # Dual-gate clustering.
    similarity_threshold: float = Field(default=0.80, ge=0.0, le=1.0)
    quiet_period_minutes: int = Field(default=30, ge=1)
    max_lifetime_hours: int = Field(default=24, ge=1)

    # Cluster-close / outbox sweep cadence.
    close_interval_seconds: int = Field(default=60, gt=0)

    # PostgreSQL pool.
    db_pool_min_size: int = Field(default=1, ge=1)
    db_pool_max_size: int = Field(default=5, ge=1)

    # LLM-assisted merge. When disabled (or the shared LLM gateway is unconfigured), ambiguous
    # clusters are marked ERROR_RETRYABLE rather than fabricated (functional document sec 9).
    llm_enabled: bool = True
    llm_prompt_version: str = "cleansing-merge-v1"
    llm_max_excerpts: int = Field(default=5, ge=1)
    llm_excerpt_chars: int = Field(default=600, gt=0)

    # LLM-assisted classification. Consulted only for an article the deterministic taxonomy could not
    # type at all (OTHER) — never for one it already resolved. Shares `llm_enabled`/the shared gateway
    # with the merge step above; this flag exists to allow disabling just the classify fallback (e.g.
    # to measure the deterministic-only accuracy floor) without also disabling merge.
    llm_classify_other: bool = True
    llm_classify_prompt_version: str = "cleansing-classify-v1"
    llm_classify_body_chars: int = Field(default=600, gt=0)
