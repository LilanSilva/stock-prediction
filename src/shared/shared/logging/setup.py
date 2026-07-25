"""structlog JSON logging configuration and correlation-id helpers.

Every service calls setup_logging() once at startup. RabbitMQ consumers call bind_correlation_id()
at the start of each handler; FastAPI apps use CorrelationIdMiddleware instead. Every emitted line
is a single JSON object including timestamp, level, service, and (when bound) correlation_id.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

_service_name = "unknown"


def _add_service(
    _logger: Any,
    _method_name: str,
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    """Add process-level service identity independently of request context."""
    event_dict["service"] = _service_name
    return event_dict


def setup_logging(service_name: str, log_level: str = "INFO") -> None:
    """Configure structlog for JSON output. Call once, before any logging."""
    global _service_name

    _service_name = service_name
    log_level_int = getattr(logging, log_level.upper(), logging.INFO)
    structlog.contextvars.clear_contextvars()

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        _add_service,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=[*shared_processors, structlog.processors.JSONRenderer()],
        wrapper_class=structlog.make_filtering_bound_logger(log_level_int),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=log_level_int)

def get_logger(name: str | None = None) -> Any:
    """Return a structlog bound logger. Pass __name__ as the name argument."""
    logger = structlog.get_logger()
    return logger.bind(logger=name) if name else logger


def bind_correlation_id(correlation_id: str, *, message_id: str | None = None) -> None:
    """Bind message identifiers to structlog contextvars for the current async context.

    Call at the start of every RabbitMQ message handler, using the message's correlation_id.
    """
    context = {"correlation_id": correlation_id}
    if message_id is not None:
        context["message_id"] = message_id
    structlog.contextvars.bind_contextvars(**context)
