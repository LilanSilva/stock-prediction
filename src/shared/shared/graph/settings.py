"""Environment-backed Neo4j connection settings."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Neo4jSettings(BaseSettings):
    """Neo4j Bolt connection settings, read from ``NEO4J_*`` environment variables."""

    model_config = SettingsConfigDict(env_prefix="NEO4J_", extra="ignore")

    uri: str = Field(default="bolt://localhost:7687")
    user: str = Field(default="neo4j")
    password: str = Field(default="")
    # Bounded connection acquisition so a graph outage surfaces quickly instead of hanging.
    connection_timeout_seconds: float = Field(default=10.0, gt=0)
    max_connection_pool_size: int = Field(default=20, gt=0)
