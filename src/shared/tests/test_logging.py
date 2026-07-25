import io
import json
import uuid
from contextlib import redirect_stdout
from typing import Any, cast

import structlog
from starlette.requests import Request

from shared.logging import bind_correlation_id, get_logger, setup_logging


def _capture_log(**kwargs: object) -> dict[str, Any]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        get_logger("test.logger").info("hello", **kwargs)
    line = buf.getvalue().strip().splitlines()[-1]
    return cast(dict[str, Any], json.loads(line))


def test_log_line_is_json_with_required_fields() -> None:
    setup_logging("unit-service", log_level="DEBUG")
    entry = _capture_log(key="value")
    assert entry["event"] == "hello"
    assert entry["service"] == "unit-service"
    assert entry["level"] == "info"
    assert "timestamp" in entry
    assert entry["key"] == "value"


def test_correlation_id_appears_after_binding() -> None:
    setup_logging("unit-service", log_level="DEBUG")
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(service="unit-service")
    bind_correlation_id("11111111-1111-4111-8111-111111111111")
    entry = _capture_log()
    assert entry["correlation_id"] == "11111111-1111-4111-8111-111111111111"


def test_correlation_id_middleware_roundtrip() -> None:
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route
    from starlette.testclient import TestClient

    from shared.logging import CorrelationIdMiddleware

    async def endpoint(_request: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/", endpoint)])
    app.add_middleware(CorrelationIdMiddleware)
    with TestClient(app) as client:
        # Provided header is echoed back.
        resp = client.get("/", headers={"X-Correlation-ID": "abc"})
        assert resp.headers["X-Correlation-ID"] == "abc"

        # Absent header -> a value is generated.
        resp2 = client.get("/")
        uuid.UUID(resp2.headers["X-Correlation-ID"], version=4)

    # Request cleanup must not remove the process-level service identity.
    assert structlog.contextvars.get_contextvars() == {}
    assert _capture_log()["service"] == "unit-service"
