"""Cleansing service configuration (environment-backed)."""

from __future__ import annotations

from typing import Literal

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
    classification_mode: Literal["title_first", "title_only", "title_with_relevant_context"] = (
        "title_first"
    )

    @property
    def processing_version(self) -> str:
        from shared.text import NORMALIZER_VERSION

        from cleansing.evidence import CLASSIFIER_VERSION

        return ":".join(
            (
                NORMALIZER_VERSION,
                CLASSIFIER_VERSION,
                self.classification_mode,
                self.embedding_backend,
                self.bge_model_name,
                str(self.embedding_dimension),
                self.nlp_backend,
                str(self.llm_enabled and self.llm_classify_other),
                self.llm_classify_prompt_version,
            )
        )

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

    # LLM classification: eligible unmapped text only, never quality/safety rejections.
    # Shares llm_enabled/the gateway with merge; this switch disables classification alone
    # so the deterministic accuracy floor can be measured without disabling merge.
    llm_classify_other: bool = True
    llm_classify_prompt_version: str = "cleansing-classify-v1"
    llm_classify_body_chars: int = Field(default=600, gt=0)
