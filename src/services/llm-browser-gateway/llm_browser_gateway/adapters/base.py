import asyncio
from typing import Protocol

from .common.types import JSON, BrowserResult, Context, Result


class BrowserTransport(Protocol):
    async def command(
        self, provider: str, context: Context, attempt_id: str, prompt: str | None
    ) -> BrowserResult: ...


class Adapter(Protocol):
    name: str
    capabilities: frozenset[str]

    async def ready(self, context: Context) -> BrowserResult: ...

    async def execute(self, body: JSON, context: Context, attempt_id: str) -> Result: ...


def remaining(context: Context) -> float:
    return max(0, context.deadline - asyncio.get_running_loop().time())
