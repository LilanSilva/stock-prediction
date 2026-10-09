from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI

from llm_browser_gateway.adapters.base import Adapter
from llm_browser_gateway.adapters.common.contract import CAPABILITIES
from llm_browser_gateway.adapters.common.formatting import parse
from llm_browser_gateway.adapters.common.types import (
    JSON,
    BrowserResult,
    Context,
    Result,
    Status,
)
from llm_browser_gateway.app import create_app
from llm_browser_gateway.config import Settings
from llm_browser_gateway.db import Availability


class MemoryStore:
    def __init__(self) -> None:
        self.states: dict[tuple[str, str], Availability] = {}
        self.active: dict[str, tuple[str, str, int]] = {}
        self.started: dict[str, datetime] = {}
        self.updated: dict[tuple[str, str], datetime] = {}
        self.unknown: set[str] = set()
        self.reservations: list[tuple[str, str, int]] = []

    async def start(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def availability(self, profile: str, provider: str) -> Availability | None:
        return self.states.get((profile, provider))

    async def reserve(
        self, profile: str, provider: str, attempt: str, request: str, slot: int = 0
    ) -> bool:
        state = self.states.get((profile, provider))
        if state is not None and not state.eligible:
            return False
        if (profile, provider, slot) in self.active.values():
            return False
        self.active[attempt] = (profile, provider, slot)
        self.started[attempt] = datetime.now(UTC)
        self.reservations.append((request, provider, slot))
        return True

    async def submitted(self, attempt: str) -> None:
        pass

    async def finish(self, profile: str, provider: str, attempt: str, result: Result) -> None:
        if result.status == Status.UNKNOWN:
            self.unknown.add(attempt)
        else:
            self.active.pop(attempt, None)
        if result.status in {Status.SUCCESS, Status.REFUSED}:
            if (
                self.updated.get((profile, provider), self.started[attempt])
                <= self.started[attempt]
            ):
                self.states.pop((profile, provider), None)
        else:
            self.updated[profile, provider] = datetime.now(UTC)
            self.states[profile, provider] = Availability(
                result.status,
                result.reset_at,
                None
                if result.status == Status.UNKNOWN
                else result.reset_at or datetime.now(UTC) + timedelta(seconds=900),
            )

    async def terminal(self, profile: str, provider: str, attempt: str) -> None:
        attempt = attempt.removesuffix("-repair")
        if attempt in self.unknown:
            self.unknown.remove(attempt)
            self.active.pop(attempt, None)
            self.states.pop((profile, provider), None)

    async def reset(self, profile: str) -> None:
        for attempt in list(self.unknown):
            if self.active.get(attempt, (None,))[0] == profile:
                self.active.pop(attempt, None)
                self.unknown.remove(attempt)
        self.states = {key: state for key, state in self.states.items() if key[0] != profile}

    async def snapshot(self, profile: str) -> dict[str, Any]:
        return {"availability": [], "active": list(self.active)}


class FakeAdapter:
    capabilities = CAPABILITIES

    def __init__(self, name: str = "chatgpt", result: Result | None = None) -> None:
        self.name = name
        self.result = result
        self.seen: list[JSON] = []
        self.probes: list[Context] = []

    async def ready(self, context: Context) -> BrowserResult:
        self.probes.append(context)
        return BrowserResult(Status.SUCCESS)

    async def execute(self, body: JSON, context: Context, attempt_id: str) -> Result:
        self.seen.append(body)
        if self.result is not None:
            return self.result
        return parse('{"kind":"final","content":"hello"}', body, self.name)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        api_key="a" * 32,
        pairing_key="b" * 32,
        database_url="postgresql://unused/test",
        extension_id="a" * 32,
        deadline_seconds=5,
    )


@pytest.fixture
def store() -> MemoryStore:
    return MemoryStore()


@pytest.fixture
def adapter() -> FakeAdapter:
    return FakeAdapter()


@pytest.fixture
def app(settings: Settings, store: MemoryStore, adapter: FakeAdapter) -> FastAPI:
    adapters: dict[str, Adapter] = {"chatgpt": adapter, "claude": FakeAdapter("claude")}
    return create_app(settings, store=store, adapters=adapters)


@pytest.fixture
async def running(app: FastAPI) -> AsyncIterator[FastAPI]:
    async with app.router.lifespan_context(app):
        yield app
