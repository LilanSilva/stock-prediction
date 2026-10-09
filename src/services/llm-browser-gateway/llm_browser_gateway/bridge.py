"""Authenticated extension transport, with separate submission acknowledgements."""

import asyncio
import hmac
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from shared.logging import get_logger

from .adapters.base import remaining
from .adapters.common.types import BrowserResult, Context, Status
from .config import Settings
from .db import Store

LOG = get_logger(__name__)
STAGES = {
    "navigation",
    "content_connection",
    "readiness",
    "fresh_conversation",
    "editor",
    "input_verification",
    "send_available",
    "send_disabled",
    "submission_ack",
    "extraction",
}


@dataclass
class Pending:
    provider: str
    future: asyncio.Future[BrowserResult]
    generation: bool


class Bridge:
    def __init__(self, settings: Settings, store: Store) -> None:
        self.settings = settings
        self.store = store
        self.socket: WebSocket | None = None
        self.extension_version: str | None = None
        self.pending: dict[str, Pending] = {}
        self.send_lock = asyncio.Lock()

    async def send(self, message: dict[str, Any]) -> None:
        async with self.send_lock:
            if self.socket is None:
                raise ConnectionError("Extension disconnected")
            await self.socket.send_json(message)

    async def serve(self, ws: WebSocket) -> None:
        expected = "chrome-extension://" + self.settings.extension_id
        if ws.headers.get("origin") != expected:
            await ws.close(code=1008)
            return
        await ws.accept()
        owned = False
        try:
            auth = await asyncio.wait_for(ws.receive_json(), 5)
            if not isinstance(auth, dict) or not isinstance(auth.get("token"), str):
                LOG.warning("browser_auth_rejected", reason="invalid_message")
                return
            if (
                auth.get("type") != "authenticate"
                or auth.get("profileId") != self.settings.profile_id
                or not hmac.compare_digest(
                    auth["token"].encode(), self.settings.pairing_key.get_secret_value().encode()
                )
                or self.socket is not None
            ):
                reason = "connection_busy" if self.socket is not None else "invalid_pairing"
                LOG.warning("browser_auth_rejected", reason=reason)
                await ws.send_json({"type": "connection_error", "code": reason})
                return
            if auth.get("protocolVersion") != 2:
                await ws.send_json({"type": "connection_error", "code": "update_required"})
                return
            self.socket = ws
            version = auth.get("extensionVersion")
            self.extension_version = (
                version
                if isinstance(version, str) and re.fullmatch(r"[0-9.]{1,32}", version)
                else None
            )
            owned = True
            LOG.info("browser_connected", extension_version=self.extension_version)
            await self.send({"type": "authenticated"})
            while True:
                msg = await asyncio.wait_for(ws.receive_json(), 45)
                if not isinstance(msg, dict):
                    continue
                kind = msg.get("type")
                if kind == "ping":
                    await self.send({"type": "pong"})
                elif kind == "reset":
                    if not self.pending:
                        await self.store.reset(self.settings.profile_id)
                        await self.send({"type": "reset_ack"})
                elif kind in {"progress", "result"}:
                    await self.handle(msg)
        except (
            WebSocketDisconnect,
            TimeoutError,
            ValueError,
            ConnectionError,
            RuntimeError,
        ) as exc:
            LOG.info("browser_connection_closed", exception_type=type(exc).__name__)
        finally:
            if owned:
                self.socket = None
                self.extension_version = None
                for entry in self.pending.values():
                    if not entry.future.done():
                        entry.future.set_result(
                            BrowserResult(
                                Status.UNKNOWN if entry.generation else Status.DISCONNECTED,
                                submitted=None if entry.generation else False,
                            )
                        )
            try:
                await ws.close(code=1008)
            except (RuntimeError, WebSocketDisconnect):
                pass

    async def handle(self, msg: dict[str, Any]) -> None:
        attempt = msg.get("attemptId")
        provider = msg.get("provider")
        if not isinstance(attempt, str) or provider not in self.settings.providers:
            return
        entry = self.pending.get(attempt)
        if entry is not None and entry.provider != provider:
            return
        if msg["type"] == "progress":
            if entry is not None and msg.get("phase") == "submitting":
                await self.store.submitted(attempt)
                await self.send({"type": "progress_ack", "attemptId": attempt})
            return
        try:
            status = Status(str(msg.get("status")))
        except (ValueError, TypeError):
            return
        submitted = msg.get("submitted")
        if submitted is not None and type(submitted) is not bool:
            return
        text = msg.get("text", "")
        if not isinstance(text, str) or len(text) > self.settings.max_body_bytes:
            status, text, submitted = Status.INVALID_OUTPUT, "", True
        reset_at = None
        raw_reset = msg.get("resetAt")
        if isinstance(raw_reset, str):
            try:
                parsed = datetime.fromisoformat(raw_reset)
                if parsed.tzinfo is not None:
                    reset_at = parsed
            except ValueError:
                pass
        model = msg.get("observedModel")
        observed = model[:128] if isinstance(model, str) else None
        result = BrowserResult(status, text, submitted, reset_at, observed)
        diagnostic = msg.get("diagnostic")
        LOG.info(
            "browser_result",
            attempt_id=attempt,
            provider=provider,
            outcome=status.value,
            submitted=submitted,
            stage=diagnostic if isinstance(diagnostic, str) and diagnostic in STAGES else None,
        )
        if entry is not None and not entry.future.done():
            entry.future.set_result(result)
        elif status != Status.UNKNOWN:
            await self.store.terminal(self.settings.profile_id, provider, attempt)
        await self.send({"type": "job_ack", "attemptId": attempt})

    async def command(
        self, provider: str, context: Context, attempt_id: str, prompt: str | None
    ) -> BrowserResult:
        if self.socket is None:
            return BrowserResult(Status.DISCONNECTED)
        entry = Pending(provider, asyncio.get_running_loop().create_future(), prompt is not None)
        self.pending[attempt_id] = entry
        dispatched = False
        try:
            await self.send(
                {
                    "type": "execute" if prompt is not None else "probe",
                    "provider": provider,
                    "attemptId": attempt_id,
                    "requestId": context.request_id,
                    "slot": context.slot,
                    "prompt": prompt,
                    "timeoutMs": int(remaining(context) * 1000),
                }
            )
            dispatched = True
            timeout = remaining(context) if prompt is not None else min(45, remaining(context))
            return await asyncio.wait_for(asyncio.shield(entry.future), timeout)
        except (TimeoutError, ConnectionError, RuntimeError):
            await self.cancel(attempt_id)
            unknown = prompt is not None and dispatched
            return BrowserResult(
                Status.UNKNOWN if unknown else Status.DISCONNECTED,
                submitted=None if unknown else False,
            )
        except asyncio.CancelledError:
            await self.cancel(attempt_id)
            raise
        finally:
            self.pending.pop(attempt_id, None)
            if not entry.future.done():
                entry.future.cancel()

    async def cancel(self, attempt_id: str) -> None:
        try:
            await self.send({"type": "cancel", "attemptId": attempt_id})
        except (ConnectionError, RuntimeError):
            pass
