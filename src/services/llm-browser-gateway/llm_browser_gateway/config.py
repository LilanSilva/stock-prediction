"""Host-side settings. Credentials remain in the repository's ignored infra/.env."""

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BROWSER_GATEWAY_", env_file=ROOT / "infra" / ".env", extra="ignore"
    )
    api_key: SecretStr = Field(min_length=24)
    pairing_key: SecretStr = Field(min_length=24)
    database_url: SecretStr
    extension_id: str = Field(pattern=r"^[a-p]{32}$")
    profile_id: str = Field(default="default", pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    priority: str = "chatgpt,claude"
    model_alias: str = "browser-auto"
    deployment: Literal["host", "container"] = "host"
    host: str = "127.0.0.1"
    port: int = Field(default=8091, ge=1024, le=65535)
    deadline_seconds: float = Field(default=180, ge=5, le=900)
    queue_size: int = Field(default=16, ge=1, le=1000)
    tabs_per_provider: int = Field(default=2, ge=1, le=4)
    retry_seconds: float = Field(default=900, ge=5, le=86400)
    max_body_bytes: int = Field(default=262144, ge=1024, le=2097152)
    retention_days: int = Field(default=7, ge=1, le=90)

    @model_validator(mode="after")
    def distinct_keys(self) -> "Settings":
        if self.deployment == "container":
            if self.host != "0.0.0.0":
                raise ValueError("Container mode requires 0.0.0.0; publish only to host loopback")
        elif self.host not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("Host mode must bind to loopback")
        if self.api_key.get_secret_value() == self.pairing_key.get_secret_value():
            raise ValueError("Use distinct API and extension pairing keys")
        if not self.providers or len(self.providers) != len(set(self.providers)):
            raise ValueError("Priority must contain distinct registered provider names")
        return self

    @property
    def providers(self) -> tuple[str, ...]:
        return tuple(name.strip() for name in self.priority.split(",") if name.strip())
