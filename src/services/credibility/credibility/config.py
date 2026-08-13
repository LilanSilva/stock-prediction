"""Environment-backed configuration for the Credibility Service."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CredibilitySettings(BaseSettings):
    """Settings read from ``CREDIBILITY_*`` plus the shared infra variables.

    Neo4j connection settings are read separately by ``shared.graph.Neo4jSettings`` from the
    conventional unprefixed ``NEO4J_*`` variables, exactly as the Prediction Service does.
    """

    model_config = SettingsConfigDict(env_prefix="CREDIBILITY_", extra="ignore")

    # Shared infra connection strings use their conventional unprefixed names.
    database_url: str = Field(validation_alias="DATABASE_URL")
    rabbitmq_url: str = Field(validation_alias="RABBITMQ_URL")
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")

    scored_queue: str = Field(default="credibility.scored")

    # Beta-Bernoulli floor: alpha/beta are never allowed below this uninformed-prior value, so a
    # seed edge that starts at 1.0/1.0 can only ever grow (T01/T02 acceptance criteria).
    # Applies to source credibility only; KG edge outcomes move `weight` (see below).
    prior_floor: float = Field(default=1.0, gt=0.0)

    # KG edge weight learning. One scored prediction moves the edge's weight by `weight_step` scaled
    # by that edge's credit share, clamped to [weight_floor, 1.0]. The step is small and the floor
    # is above zero on purpose: an expert-asserted edge should degrade only under sustained
    # evidence, and become negligible rather than vanish.
    weight_step: float = Field(default=0.02, gt=0.0, le=1.0)
    weight_floor: float = Field(default=0.05, ge=0.0, lt=1.0)

    db_pool_min_size: int = Field(default=1, ge=1)
    db_pool_max_size: int = Field(default=5, ge=1)

    # Offline structure learner scheduled in-process. Disable to run it only as a manual CLI batch.
    learning_enabled: bool = Field(default=True)
    learning_interval_hours: int = Field(default=24, ge=1)
