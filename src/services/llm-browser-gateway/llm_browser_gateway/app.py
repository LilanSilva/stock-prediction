import asyncio
import hmac
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from shared.logging import get_logger
from starlette.exceptions import HTTPException

from .adapters.base import Adapter
from .adapters.browser import registry
from .adapters.common.contract import validate
from .adapters.common.json_codec import loads
from .adapters.common.types import Context, GatewayError, error_body
from .bridge import Bridge
from .config import Settings
from .db import PostgresStore, Store
from .engine import Engine


def create_app(
    settings: Settings, *, store: Store | None = None, adapters: dict[str, Adapter] | None = None
) -> FastAPI:
    storage = store or PostgresStore(
        settings.database_url.get_secret_value(), settings.retry_seconds, settings.retention_days
    )
    bridge = Bridge(settings, storage)
    registered: dict[str, Adapter] = dict(registry(bridge)) if adapters is None else adapters
    engine = Engine(settings, storage, registered)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            await storage.start()
            yield
        finally:
            await storage.close()

    app = FastAPI(
        title="LLM Browser Gateway",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.engine, app.state.bridge, app.state.store = engine, bridge, storage

    def authenticate(request: Request) -> None:
        value = request.headers.get("authorization", "")
        expected = "Bearer " + settings.api_key.get_secret_value()
        if not hmac.compare_digest(value.encode(), expected.encode()):
            raise GatewayError("Invalid gateway API key", 401, "authentication_error")

    @app.exception_handler(GatewayError)
    async def gateway_error(_: Request, exc: GatewayError) -> JSONResponse:
        return JSONResponse(error_body(exc.message, exc.code, exc.param), exc.http_status)

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            error_body("Route or method unavailable", "invalid_request"), exc.status_code
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(error_body("Invalid request", "invalid_request"), 400)

    @app.exception_handler(Exception)
    async def unexpected_error(_: Request, exc: Exception) -> JSONResponse:
        get_logger(__name__).error("gateway_internal_failure", exception_type=type(exc).__name__)
        return JSONResponse(error_body("Gateway unavailable", "internal_error"), 503)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/status")
    async def status(request: Request) -> JSONResponse:
        authenticate(request)
        from fastapi.encoders import jsonable_encoder

        return JSONResponse(
            jsonable_encoder(
                {
                    "extension_connected": bridge.socket is not None,
                    "extension_version": bridge.extension_version,
                    "profile_id": settings.profile_id,
                    "priority": settings.providers,
                    "tabs_per_provider": settings.tabs_per_provider,
                    "requests": engine.active,
                    **await storage.snapshot(settings.profile_id),
                }
            )
        )

    @app.websocket("/bridge")
    async def extension(ws: WebSocket) -> None:
        await bridge.serve(ws)

    @app.post("/v1/chat/completions")
    async def complete(request: Request) -> JSONResponse:
        authenticate(request)
        if request.query_params:
            raise GatewayError("Query parameters are unsupported")
        if request.headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
            raise GatewayError("Content-Type must be application/json")
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > settings.max_body_bytes:
                raise GatewayError(
                    "Request body exceeds configured limit", 413, "request_too_large"
                )
        try:
            body = loads(raw)
            required = validate(body, settings.model_alias)
        except (ValueError, UnicodeError, RecursionError, TypeError, KeyError) as exc:
            raise GatewayError("Malformed JSON request") from exc
        context = Context(
            uuid.uuid4().hex,
            asyncio.get_running_loop().time() + settings.deadline_seconds,
            settings.profile_id,
            required,
        )
        task = asyncio.create_task(engine.complete(body, context))
        try:
            while not task.done():
                await asyncio.wait({task}, timeout=0.1)
                if await request.is_disconnected():
                    task.cancel()
                    raise GatewayError("Client disconnected", 499, "client_disconnected")
            try:
                result = task.result()
            except TimeoutError as exc:
                raise GatewayError(
                    "Gateway request deadline expired", 504, "deadline_exceeded"
                ) from exc
            return JSONResponse(
                result.body, result.http_status, headers={"x-request-id": context.request_id}
            )
        finally:
            if not task.done():
                task.cancel()
            with suppress(asyncio.CancelledError, GatewayError, TimeoutError):
                await task

    return app
