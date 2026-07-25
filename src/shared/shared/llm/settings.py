"""LLM gateway configuration (provider-configurable by environment)."""

import os

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from shared.llm.exceptions import LLMConfigurationError


class LLMSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LLM_", extra="ignore")

    provider: str = ""
    model: str = ""
    api_key_env: str | None = None
    max_input_tokens: int = Field(default=6000, gt=0)
    max_output_tokens: int = Field(default=512, gt=0)
    timeout_seconds: float = Field(default=30.0, gt=0)
    transport_retries: int = Field(default=2, ge=0, le=5)
    malformed_output_retries: int = Field(default=1, ge=0, le=1)
    cache_enabled: bool = True

    def require_configured(self) -> None:
        """Fail fast when provider/model are not configured (used by service readiness)."""
        if not self.provider:
            raise LLMConfigurationError("LLM_PROVIDER is not configured")
        if not self.model:
            raise LLMConfigurationError("LLM_MODEL is not configured")

    def resolve_api_key(self) -> str | None:
        """Resolve the API key from LLM_API_KEY_ENV if set, else the provider's default variable."""
        if self.api_key_env:
            return os.environ.get(self.api_key_env)
        default_var = {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
        }.get(self.provider.lower())
        # bedrock uses AWS credentials/profile rather than a single API key variable.
        return os.environ.get(default_var) if default_var else None

    def require_api_key(self) -> str:
        """Return the configured provider key or fail readiness without exposing its name/value."""
        api_key = self.resolve_api_key()
        if not api_key:
            raise LLMConfigurationError(
                f"API key is not configured for LLM_PROVIDER={self.provider!r}"
            )
        return api_key
