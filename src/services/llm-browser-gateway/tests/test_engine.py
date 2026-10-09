import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from conftest import FakeAdapter, MemoryStore
from test_contract import BASE

from llm_browser_gateway.adapters.base import Adapter
from llm_browser_gateway.adapters.browser import BrowserAdapter
from llm_browser_gateway.adapters.common.types import (
    JSON,
    BrowserResult,
    Context,
    GatewayError,
    Result,
    Status,
    failure,
)
from llm_browser_gateway.config import Settings
from llm_browser_gateway.db import Availability
from llm_browser_gateway.engine import Engine


def context() -> Context:
    return Context("request", asyncio.get_running_loop().time() + 2, "default", frozenset({"text"}))


async def test_two_tabs_run_concurrently_and_third_request_queues(
    settings: Settings, store: MemoryStore
) -> None:
    settings.priority = "chatgpt"
    gates = [asyncio.Event() for _ in range(3)]
    two_entered, third_entered = asyncio.Event(), asyncio.Event()
    slots: list[int] = []

    class ConcurrentAdapter(FakeAdapter):
        async def execute(self, body: JSON, ctx: Context, attempt_id: str) -> Result:
            index = len(slots)
            slots.append(ctx.slot)
            if len(slots) == 2:
                two_entered.set()
            if len(slots) == 3:
                third_entered.set()
            await gates[index].wait()
            return await super().execute(body, ctx, attempt_id)

    adapter = ConcurrentAdapter()
    engine = Engine(settings, store, {"chatgpt": adapter})
    first = asyncio.create_task(engine.complete(BASE, context()))
    second = asyncio.create_task(engine.complete(BASE, context()))
    await asyncio.wait_for(two_entered.wait(), 1)
    assert sorted(slots) == [0, 1]
    third = asyncio.create_task(engine.complete(BASE, context()))
    await asyncio.sleep(0.02)
    assert engine.active == 3 and len(slots) == 2
    gates[0].set()
    await asyncio.wait_for(third_entered.wait(), 1)
    assert slots == [0, 1, 0]
    gates[1].set()
    gates[2].set()
    results = await asyncio.gather(first, second, third)
    assert all(result.status == Status.SUCCESS for result in results)
    assert all(body is BASE for body in adapter.seen)
    assert not store.active


@pytest.mark.parametrize(
    "status",
    [
        Status.RATE_LIMITED,
        Status.UI_CHANGED,
        Status.DISCONNECTED,
        Status.TEMPORARY,
        Status.LOGIN_REQUIRED,
        Status.VERIFICATION_REQUIRED,
    ],
)
@pytest.mark.parametrize(
    ("phase", "submitted"), [("ready", False), ("execute", False), ("execute", True)]
)
async def test_assigned_request_never_changes_provider(
    settings: Settings, store: MemoryStore, status: Status, phase: str, submitted: bool
) -> None:
    class FailingAdapter(FakeAdapter):
        async def ready(self, ctx: Context) -> BrowserResult:
            await super().ready(ctx)
            return BrowserResult(status) if phase == "ready" else BrowserResult(Status.SUCCESS)

    first = FailingAdapter(result=failure(status, submitted=submitted))
    second = FakeAdapter("claude")
    adapters: dict[str, Adapter] = {"chatgpt": first, "claude": second}
    body = dict(BASE)
    engine = Engine(settings, store, adapters)
    reply = await engine.complete(body, context())
    assert reply.status == status and reply.body["error"]["code"] == status
    assert len(first.probes) == 1
    assert not second.probes and not second.seen
    assert store.reservations == [("request", "chatgpt", 0)]
    if phase == "execute":
        assert first.seen[0] is body
    else:
        assert not first.seen
    assert store.states["default", "chatgpt"].reason == status
    assert not store.active

    # Availability changes affect a new request, never the already-assigned one.
    next_body = dict(BASE)
    next_reply = await engine.complete(next_body, replace(context(), request_id="new-request"))
    assert next_reply.body["model"] == "claude-web"
    assert len(first.probes) == 1 and len(second.probes) == 1
    assert second.seen[0] is next_body
    assert store.reservations == [("request", "chatgpt", 0), ("new-request", "claude", 0)]


async def test_busy_provider_is_skipped_before_request_assignment(
    settings: Settings, store: MemoryStore
) -> None:
    first, second = FakeAdapter(), FakeAdapter("claude")
    assert await store.reserve("default", "chatgpt", "busy-0", "other-0", 0)
    assert await store.reserve("default", "chatgpt", "busy-1", "other-1", 1)
    body = dict(BASE)
    result = await Engine(settings, store, {"chatgpt": first, "claude": second}).complete(
        body, context()
    )
    assert result.body["model"] == "claude-web"
    assert not first.probes and not first.seen
    assert second.seen[0] is body
    assert [item for item in store.reservations if item[0] == "request"] == [
        ("request", "claude", 0)
    ]


async def test_uncertain_submission_never_falls_back(
    settings: Settings, store: MemoryStore
) -> None:
    first = FakeAdapter(result=failure(Status.UNKNOWN, submitted=None))
    second = FakeAdapter("claude")
    reply = await Engine(settings, store, {"chatgpt": first, "claude": second}).complete(
        BASE, context()
    )
    assert reply.status == Status.UNKNOWN and not second.seen
    assert store.unknown and store.active
    attempt = next(iter(store.unknown))
    await store.terminal("default", "chatgpt", attempt)
    assert not store.active


async def test_due_cooldown_gets_rechecked(settings: Settings, store: MemoryStore) -> None:
    store.states["default", "chatgpt"] = Availability(
        "rate_limited", next_check_at=datetime.now(UTC) - timedelta(seconds=1)
    )
    first = FakeAdapter()
    result = await Engine(
        settings, store, {"chatgpt": first, "claude": FakeAdapter("claude")}
    ).complete(BASE, context())
    assert result.status == Status.SUCCESS and first.seen
    assert ("default", "chatgpt") not in store.states


async def test_all_limited_and_queue_bounds(settings: Settings, store: MemoryStore) -> None:
    for name in settings.providers:
        store.states["default", name] = Availability(
            "rate_limited", next_check_at=datetime.now(UTC) + timedelta(hours=1)
        )
    engine = Engine(settings, store, {"chatgpt": FakeAdapter(), "claude": FakeAdapter("claude")})
    assert (await engine.complete(BASE, context())).http_status == 429
    engine.active = settings.queue_size
    with pytest.raises(GatewayError, match="queue is full"):
        await engine.complete(BASE, context())


@pytest.mark.parametrize(
    "reasons",
    [
        (Status.UI_CHANGED, Status.UI_CHANGED),
        (Status.LOGIN_REQUIRED, Status.VERIFICATION_REQUIRED),
        (Status.RATE_LIMITED, Status.UI_CHANGED),
        (Status.DISCONNECTED, Status.UNKNOWN),
    ],
)
async def test_blocked_providers_are_unavailable_not_disconnected(
    settings: Settings, store: MemoryStore, reasons: tuple[Status, Status]
) -> None:
    first, second = FakeAdapter(), FakeAdapter("claude")
    for name, reason in zip(settings.providers, reasons, strict=True):
        store.states["default", name] = Availability(
            reason,
            next_check_at=None
            if reason == Status.UNKNOWN
            else datetime.now(UTC) + timedelta(minutes=15),
        )
    original = dict(store.states)
    engine = Engine(settings, store, {"chatgpt": first, "claude": second})
    reply = await engine.complete(BASE, context())
    assert reply.http_status == 503
    assert reply.status == Status.TEMPORARY
    assert reply.body["error"]["code"] == "temporary_unavailable"
    assert not first.probes and not second.probes
    assert not store.reservations and store.states == original

    # Only a due provider becomes eligible; other cooldowns remain intact.
    store.states["default", "chatgpt"] = replace(
        original["default", "chatgpt"], next_check_at=datetime.now(UTC) - timedelta(seconds=1)
    )
    recovered = await engine.complete(BASE, replace(context(), request_id="after-cooldown"))
    assert recovered.status == Status.SUCCESS
    assert len(first.probes) == 1 and not second.probes
    assert store.states["default", "claude"] == original["default", "claude"]


async def test_adapter_repairs_once_with_same_history() -> None:
    class Transport:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        async def command(
            self, provider: str, ctx: Context, attempt: str, prompt: str | None
        ) -> BrowserResult:
            assert prompt is not None
            self.prompts.append(prompt)
            return BrowserResult(
                Status.SUCCESS,
                "bad json" if len(self.prompts) == 1 else '{"kind":"final","content":"repaired"}',
                True,
            )

    transport = Transport()
    response = await BrowserAdapter("chatgpt", transport).execute(BASE, context(), "attempt")
    assert response.http_status == 200 and len(transport.prompts) == 2
    assert (
        transport.prompts[0].split("REQUEST:\n")[1] == transport.prompts[1].split("REQUEST:\n")[1]
    )


@pytest.mark.parametrize("status", [Status.INVALID_OUTPUT, Status.INVALID_REQUEST])
async def test_request_error_does_not_disable_provider(
    settings: Settings, store: MemoryStore, status: Status
) -> None:
    store.states["default", "claude"] = Availability(
        Status.RATE_LIMITED, next_check_at=datetime.now(UTC) + timedelta(hours=1)
    )
    first = FakeAdapter(result=failure(status, submitted=True))
    second = FakeAdapter("claude")
    engine = Engine(settings, store, {"chatgpt": first, "claude": second})
    failed = await engine.complete(BASE, context())
    assert failed.status == status and failed.http_status in {400, 502}
    assert not second.probes and not store.active
    assert ("default", "chatgpt") not in store.states
    first.result = None
    reply = await engine.complete(BASE, replace(context(), request_id="next-request"))
    assert reply.status == Status.SUCCESS and reply.body["model"] == "chatgpt-web"
    assert not second.probes
    assert store.states["default", "claude"].reason == Status.RATE_LIMITED


async def test_output_rejection_logs_metadata_without_response_text(
    capsys: pytest.CaptureFixture[str],
) -> None:
    private_output = "private response must not appear in service logs"

    class Transport:
        async def command(
            self, provider: str, ctx: Context, attempt: str, prompt: str | None
        ) -> BrowserResult:
            return BrowserResult(Status.SUCCESS, private_output, True)

    response = await BrowserAdapter("chatgpt", Transport()).execute(BASE, context(), "diagnostic")
    assert response.status == Status.INVALID_OUTPUT and response.http_status == 502
    captured = capsys.readouterr().out
    assert "browser_output_rejected" in captured and "JSONDecodeError" in captured
    assert "diagnostic" in captured and "output_chars" in captured
    assert private_output not in captured


@pytest.mark.parametrize("status", [Status.REFUSED, Status.INVALID_OUTPUT])
async def test_terminal_output_does_not_cycle_providers(
    settings: Settings, store: MemoryStore, status: Status
) -> None:
    expected = failure(status, submitted=True)
    first = FakeAdapter(result=expected)
    second = FakeAdapter("claude")
    result = await Engine(settings, store, {"chatgpt": first, "claude": second}).complete(
        BASE, context()
    )
    assert result is expected and result.body is expected.body and not second.seen


async def test_deadline_quarantines_active_attempt(settings: Settings, store: MemoryStore) -> None:
    class SlowAdapter(FakeAdapter):
        async def execute(self, body: JSON, context: Context, attempt_id: str) -> Result:
            await asyncio.sleep(10)
            return await super().execute(body, context, attempt_id)

    second = FakeAdapter("claude")
    ctx = Context("deadline", asyncio.get_running_loop().time() + 0.02, "default")
    with pytest.raises(TimeoutError):
        await Engine(settings, store, {"chatgpt": SlowAdapter(), "claude": second}).complete(
            BASE, ctx
        )
    assert store.active and store.unknown and not second.seen


async def test_cancelled_probe_releases_reservation(settings: Settings, store: MemoryStore) -> None:
    entered = asyncio.Event()

    class SlowProbe(FakeAdapter):
        async def ready(self, context: Context) -> BrowserResult:
            entered.set()
            await asyncio.sleep(10)
            return BrowserResult(Status.SUCCESS)

    engine = Engine(settings, store, {"chatgpt": SlowProbe(), "claude": FakeAdapter("claude")})
    task = asyncio.create_task(engine.complete(BASE, context()))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not store.active and not store.unknown and engine.active == 0


async def test_required_capabilities_filter_priority(
    settings: Settings, store: MemoryStore
) -> None:
    first = FakeAdapter()
    first.capabilities = frozenset({"text"})
    second = FakeAdapter("claude")
    ctx = Context(
        "capabilities",
        asyncio.get_running_loop().time() + 2,
        "default",
        frozenset({"text", "json", "tools"}),
    )
    result = await Engine(settings, store, {"chatgpt": first, "claude": second}).complete(BASE, ctx)
    assert result.status == Status.SUCCESS and not first.seen and second.seen
