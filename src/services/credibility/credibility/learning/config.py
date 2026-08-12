"""Environment-backed configuration for the offline learning batch.

Separate from ``CredibilitySettings`` so the always-on consumer's config is untouched. Reuses the
shared ``DATABASE_URL``/``LOG_LEVEL`` and adds ``CREDIBILITY_LEARNING_*`` tuning knobs. Neo4j
connection settings come from ``shared.graph.Neo4jSettings`` (unprefixed ``NEO4J_*``), as elsewhere.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LearningSettings(BaseSettings):
    """Tuning + connection settings for ``python -m credibility.learning.run``."""

    model_config = SettingsConfigDict(env_prefix="CREDIBILITY_LEARNING_", extra="ignore")

    database_url: str = Field(validation_alias="DATABASE_URL")
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")

    # Mean signed daily return must clear ±deadband for a directional edge; else NEUTRAL (dropped).
    deadband: float = Field(default=0.002, ge=0.0)
    # A (factor, condition, asset) group needs at least this many observations to be estimated.
    min_samples: int = Field(default=5, ge=1)
    # Only events first seen within this many days are considered.
    lookback_days: int = Field(default=30, ge=1)
    # Days of closes used to calculate per-asset historical daily volatility.
    volatility_lookback_days: int = Field(default=30, ge=2)
    # A move is abnormal when |actual_return| >= abnormal_threshold * historical_volatility.
    # Abnormal samples bypass min_samples and are written to KG with a single observation.
    abnormal_threshold: float = Field(default=2.0, gt=0.0)

    db_pool_min_size: int = Field(default=1, ge=1)
    db_pool_max_size: int = Field(default=5, ge=1)

    # Set to False to run only the CAUSES-edge path during rollout/debugging.
    correlation_learning_enabled: bool = Field(default=True)
