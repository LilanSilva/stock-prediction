"""LLM gateway exception types."""


class LLMError(Exception):
    """Base class for gateway failures."""


class LLMConfigurationError(LLMError):
    """Raised at startup/readiness when provider configuration or credentials are missing."""


class LLMTransportError(LLMError):
    """Raised when the provider fails with a transport / 429 / 5xx error after bounded retry."""


class LLMMalformedOutputError(LLMError):
    """Raised when structured output cannot be validated after the allowed retry."""
