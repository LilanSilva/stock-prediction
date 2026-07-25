"""Structured logging and correlation-id propagation.

`CorrelationIdMiddleware` requires Starlette (the optional `web` extra). It is imported lazily so
queue-consumer services that never install `web` can still import setup_logging/get_logger/
bind_correlation_id from this package.
"""

from typing import TYPE_CHECKING, Any

from shared.logging.setup import bind_correlation_id, get_logger, setup_logging

if TYPE_CHECKING:
    from shared.logging.middleware import CorrelationIdMiddleware

__all__ = [
    "CorrelationIdMiddleware",
    "bind_correlation_id",
    "get_logger",
    "setup_logging",
]


def __getattr__(name: str) -> Any:
    if name == "CorrelationIdMiddleware":
        from shared.logging.middleware import CorrelationIdMiddleware

        return CorrelationIdMiddleware
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
