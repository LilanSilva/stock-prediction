# T04: Structured Logging & Correlation ID Middleware

## Context

This task sets up structured JSON logging and correlation ID propagation for the entire system. Every service logs in JSON format using `structlog` so logs can be aggregated and queried (e.g., by correlation ID to trace a piece of news from ingestion to credibility update). The FastAPI middleware injects correlation IDs into each request context so all log entries within a request carry the same ID.

This task belongs to the shared library story (S02). Every service and the API Gateway will call `setup_logging()` at startup and import the middleware.

## Background

### structlog overview

`structlog` is a Python logging library that adds structured key-value context to log entries. When configured with `JSONRenderer`, it outputs each log entry as a single JSON line — ideal for log aggregation tools.

Key structlog concepts:
- `structlog.get_logger()` — returns a bound logger
- `log.bind(key=value)` — returns a new logger with extra context fields
- `structlog.contextvars.bind_contextvars(key=value)` — binds to the current async context (persists within a coroutine)
- `structlog.contextvars.clear_contextvars()` — clears context between requests

### Correlation ID flow

```
Ingestion creates correlation_id (UUID4)
  → ArticleIngested.correlation_id
  → bound to structlog context in every consumer
  → EventDetected.correlation_id (same value)
  → PredictionMade.correlation_id (same value)
  → ... all the way to PredictionScored
```

For RabbitMQ consumers: bind the correlation_id from the incoming message before calling the processing function.
For FastAPI handlers: extract from request header `X-Correlation-ID` (or generate a new one if absent).

### Log entry format

Every JSON log entry must include these fields:
- `timestamp` — ISO 8601 UTC
- `level` — `debug`, `info`, `warning`, `error`, `critical`
- `service` — name of the service (e.g., `ingestion`, `cleansing`)
- `correlation_id` — UUID4 string (if available)
- `event` — the log message string
- Any additional context fields added by the caller

## Inputs

- Service name string (passed to `setup_logging()` at startup)
- Incoming AMQP message correlation_id (extracted by RabbitMQ consumer code)
- HTTP request header `X-Correlation-ID` (extracted by FastAPI middleware)
- `LOG_LEVEL` environment variable (default: `INFO`)

## Outputs

- `src/shared/logging/setup.py` — `setup_logging()` function
- `src/shared/logging/middleware.py` — `CorrelationIdMiddleware` FastAPI middleware class
- `src/shared/logging/__init__.py` — re-exports `setup_logging`, `CorrelationIdMiddleware`, `get_logger`
- `tests/test_logging.py` — pytest tests

## Technical Requirements

### Dependencies (add to `src/shared/pyproject.toml`)

```toml
dependencies = [
    "pydantic>=2.7,<3",
    "pydantic-settings>=2.3,<3",
    "aio-pika>=9.4,<10",
    "anthropic>=0.30,<1",
    "tenacity>=8.3,<9",
    "structlog>=24.1,<25",
    "fastapi>=0.111,<1",
    "starlette>=0.37,<1",
]
```

### `src/shared/logging/setup.py`

```python
import logging
import sys
import structlog
from pydantic_settings import BaseSettings

class LoggingSettings(BaseSettings):
    log_level: str = "INFO"
    service_name: str = "unknown"

def setup_logging(service_name: str, log_level: str = "INFO") -> None:
    """
    Configure structlog for JSON output.
    Must be called once at service startup, before any logging.
    """
    log_level_int = getattr(logging, log_level.upper(), logging.INFO)
    
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    
    structlog.configure(
        processors=shared_processors + [
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level_int),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )
    
    # Also configure stdlib logging to use structlog formatting
    # (for third-party libraries that use logging.getLogger)
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level_int,
    )
    
    # Bind the service name globally
    structlog.contextvars.bind_contextvars(service=service_name)

def get_logger(name: str | None = None) -> structlog.BoundLogger:
    """Get a structlog bound logger. Pass __name__ as the name argument."""
    return structlog.get_logger(name)
```

### `src/shared/logging/middleware.py`

```python
import uuid
from typing import Callable, Awaitable
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
import structlog

CORRELATION_ID_HEADER = "X-Correlation-ID"

class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """
    FastAPI/Starlette middleware that:
    1. Reads X-Correlation-ID from the request header (or generates a new UUID4)
    2. Binds the correlation_id to structlog contextvars for the request duration
    3. Adds X-Correlation-ID to the response header
    4. Clears the contextvars after the response (prevents leaking to next request)
    """
    
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        correlation_id = request.headers.get(CORRELATION_ID_HEADER) or str(uuid.uuid4())
        
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            correlation_id=correlation_id,
            http_method=request.method,
            http_path=request.url.path,
        )
        
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.clear_contextvars()
        
        response.headers[CORRELATION_ID_HEADER] = correlation_id
        return response
```

### Utility function for RabbitMQ consumers

Add to `src/shared/logging/setup.py`:

```python
def bind_correlation_id(correlation_id: str) -> None:
    """
    Bind correlation_id to structlog contextvars.
    Call this at the start of every RabbitMQ message handler:
    
        from shared.logging import bind_correlation_id
        
        async def handle_message(message: IncomingMessage) -> None:
            msg = ArticleIngested.from_amqp_body(message.body)
            bind_correlation_id(msg.correlation_id)
            logger.info("processing article", article_id=msg.article_id)
    """
    structlog.contextvars.bind_contextvars(correlation_id=correlation_id)
```

### Example log output

The following is the expected JSON format for a single log entry:

```json
{
  "timestamp": "2025-06-30T12:34:56.789000Z",
  "level": "info",
  "service": "cleansing",
  "correlation_id": "550e8400-e29b-41d4-a716-446655440000",
  "event": "event detected after deduplication",
  "event_id": "a1b2c3d4-...",
  "source_count": 3,
  "logger": "services.cleansing.processor"
}
```

### `src/shared/logging/__init__.py`

Re-export the key symbols:

```python
from .setup import setup_logging, get_logger, bind_correlation_id
from .middleware import CorrelationIdMiddleware

__all__ = ["setup_logging", "get_logger", "bind_correlation_id", "CorrelationIdMiddleware"]
```

## Acceptance Criteria

1. `from shared.logging import setup_logging, get_logger, bind_correlation_id, CorrelationIdMiddleware` imports without error.
2. After calling `setup_logging('test-service')`, calling `get_logger(__name__).info('hello', key='value')` writes a single JSON line to stdout.
3. The JSON log line contains `timestamp`, `level`, `service`, and `event` fields.
4. After `bind_correlation_id('my-uuid')`, the `correlation_id` field appears in every subsequent log entry in that async context.
5. `CorrelationIdMiddleware` correctly extracts `X-Correlation-ID` from a test request (using `starlette.testclient.TestClient`).
6. When `X-Correlation-ID` is absent from the request, the middleware generates a new UUID4 and adds it to the response.
7. After the response is returned, `correlation_id` is cleared from structlog context (does not leak to next request).
8. The `service` name bound by `setup_logging()` appears in every log entry, not just the first one.
9. `pytest tests/test_logging.py -v` passes.
10. `mypy src/shared/logging/ --strict` returns 0 errors.

## Implementation Notes

- `structlog.contextvars` uses Python's `contextvars.ContextVar` under the hood, which is async-safe: each asyncio task has its own copy of the context. This means binding a correlation_id in one async task does not bleed into another task — which is exactly the behavior needed for concurrent message processing.
- The `clear_contextvars()` call in the middleware's `finally` block is important — without it, context from one HTTP request can leak into the next request if the same asyncio task is reused (which FastAPI does).
- For RabbitMQ consumers, the pattern is: `bind_correlation_id(msg.correlation_id)` at the start of each message handler. There is no equivalent of `clear_contextvars()` needed between messages because each message is processed in sequence within a single consumer coroutine and the next `bind_correlation_id()` call overwrites the previous value.
- `structlog.processors.format_exc_info` ensures that exception tracebacks are included in the `exc_info` field as a JSON string, not printed to stderr separately. This keeps log output clean for aggregation.
- `cache_logger_on_first_use=True` in `structlog.configure()` is a performance optimization. It is safe to use after `setup_logging()` is called, but means you cannot call `structlog.configure()` again after the first log call without resetting the cache.
- For services that do not use FastAPI (i.e., the six queue-consumer services), only `setup_logging()`, `get_logger()`, and `bind_correlation_id()` are needed. The `CorrelationIdMiddleware` is only imported by the API Gateway service.
- Add a `conftest.py` to `tests/` that calls `setup_logging('test', log_level='DEBUG')` in a session-scoped fixture so structlog is initialized before all tests run.

## Definition of Done

- [x] `src/shared/logging/setup.py` implements `setup_logging`, `get_logger`, `bind_correlation_id`
- [x] `src/shared/logging/middleware.py` implements `CorrelationIdMiddleware`
- [x] `src/shared/logging/__init__.py` re-exports all four public symbols
- [x] `src/shared/pyproject.toml` includes `structlog>=24.1,<25` and `fastapi>=0.111,<1`
- [x] Log output is valid JSON with all required fields
- [x] `correlation_id` propagates through async context correctly
- [x] Middleware adds `X-Correlation-ID` to response headers
- [x] `pytest tests/test_logging.py -v` passes
- [x] `mypy src/shared/logging/ --strict` returns 0 errors
- [x] `ruff check src/shared/logging/` returns 0 violations
