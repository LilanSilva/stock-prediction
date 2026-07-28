"""LLM gateway configuration (provider-configurable by environment)."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from shared.llm.exceptions import LLMConfigurationError


class LLMSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LLM_", extra="ignore")

    provider: str = ""
    model: str = ""
    api_key: str | None = None
    base_url: str | None = None
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
        """Return the single configured API key (LLM_API_KEY), or None when unset/empty."""
        return self.api_key or None

    def resolve_base_url(self) -> str | None:
        """Return the configured base URL (LLM_BASE_URL) or None to use the provider default.

        Set this for any OpenAI-compatible provider that is not api.openai.com (e.g. Kimi/Moonshot,
        Together, Azure OpenAI, a local vLLM server).
        """
        return self.base_url or None

    def require_api_key(self) -> str:
        """Return the configured provider key or fail readiness without exposing its name/value."""
        api_key = self.resolve_api_key()
        if not api_key:
            raise LLMConfigurationError(
                f"API key is not configured for LLM_PROVIDER={self.provider!r} (set LLM_API_KEY)"
            )
        return api_key
