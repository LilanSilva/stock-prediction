"""Provider-neutral routing; public payloads belong to the caller and adapters."""

import asyncio
import time
import uuid
from dataclasses import replace

from shared.logging import get_logger

from .adapters.base import Adapter, remaining
from .adapters.common.types import JSON, Context, GatewayError, Result, Status, failure
from .config import Settings
from .db import Store

LOG = get_logger(__name__)


class Engine:
    def __init__(self, settings: Settings, store: Store, adapters: dict[str, Adapter]) -> None:
        self.settings, self.store, self.adapters = settings, store, adapters
        if set(settings.providers) - set(adapters):
            raise ValueError("Priority contains an unregistered adapter")
        self.active = 0

    async def complete(self, body: JSON, context: Context) -> Result:
        if self.active >= self.settings.queue_size:
            raise GatewayError("Gateway queue is full", 503, "queue_full")
        self.active += 1
        try:
            return await self.route(body, context)
        finally:
            self.active -= 1

    async def route(self, body: JSON, context: Context) -> Result:
        eligible = [
            self.adapters[name]
            for name in self.settings.providers
            if context.required <= self.adapters[name].capabilities
        ]
        if not eligible:
            raise GatewayError(
                "No adapter supports the requested capabilities", 400, "unsupported_capability"
            )
        while remaining(context) > 0:
            busy = False
            limited = True
            blocked: dict[str, str] = {}
            for adapter in eligible:
                availability = await self.store.availability(context.profile_id, adapter.name)
                if availability is not None and not availability.eligible:
                    blocked[adapter.name] = availability.reason
                    limited = limited and availability.reason == Status.RATE_LIMITED
                    continue
                limited = False
                attempt = uuid.uuid4().hex
                selected_slot = None
                for slot in range(self.settings.tabs_per_provider):
                    if await self.store.reserve(
                        context.profile_id, adapter.name, attempt, context.request_id, slot
                    ):
                        selected_slot = slot
                        break
                if selected_slot is None:
                    busy = True
                    continue
                execution_context = replace(context, slot=selected_slot)
                started = time.monotonic()
                execution_started = False
                try:
                    ready = await adapter.ready(execution_context)
                    if ready.status == Status.SUCCESS:
                        execution_started = True
                        async with asyncio.timeout(remaining(context)):
                            last = await adapter.execute(body, execution_context, attempt)
                    else:
                        last = failure(ready.status, submitted=ready.submitted)
                        last = Result(
                            last.body,
                            last.http_status,
                            last.status,
                            last.submitted,
                            last.retry_safe,
                            ready.reset_at,
                        )
                except (asyncio.CancelledError, TimeoutError):
                    await asyncio.shield(
                        self.store.finish(
                            context.profile_id,
                            adapter.name,
                            attempt,
                            failure(Status.UNKNOWN, submitted=None)
                            if execution_started
                            else failure(Status.DISCONNECTED),
                        )
                    )
                    raise
                except Exception:
                    # An unexpected provider exception cannot establish whether submission occurred.
                    last = failure(Status.UNKNOWN, submitted=None)
                await self.store.finish(context.profile_id, adapter.name, attempt, last)
                LOG.info(
                    "browser_attempt",
                    request_id=context.request_id,
                    provider=adapter.name,
                    slot=selected_slot,
                    outcome=last.status.value,
                    elapsed_ms=round((time.monotonic() - started) * 1000),
                )
                # Reservation binds this HTTP request to one provider, including probe failures.
                # Persist availability for later requests, but never redispatch this one.
                return last
            if not busy:
                outcome = Status.RATE_LIMITED if limited else Status.TEMPORARY
                LOG.warning(
                    "browser_providers_unavailable",
                    request_id=context.request_id,
                    reasons=blocked,
                    outcome=outcome.value,
                )
                return failure(outcome)
            await asyncio.sleep(min(0.1, remaining(context)))
        raise GatewayError("Gateway request deadline expired", 504, "deadline_exceeded")
