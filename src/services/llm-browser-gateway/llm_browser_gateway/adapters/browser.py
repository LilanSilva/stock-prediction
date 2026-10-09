import uuid

from .base import BrowserTransport, remaining
from .common.contract import CAPABILITIES
from .common.formatting import parse, render
from .common.types import JSON, BrowserResult, Context, GatewayError, Result, Status, failure


class BrowserAdapter:
    capabilities = CAPABILITIES

    def __init__(self, name: str, transport: BrowserTransport) -> None:
        self.name = name
        self.transport = transport

    async def ready(self, context: Context) -> BrowserResult:
        return await self.transport.command(self.name, context, "probe-" + uuid.uuid4().hex, None)

    async def execute(self, body: JSON, context: Context, attempt_id: str) -> Result:
        for repair in (False, True):
            if remaining(context) <= 0:
                return failure(Status.TEMPORARY)
            result = await self.transport.command(
                self.name,
                context,
                attempt_id + ("-repair" if repair else ""),
                render(body, repair=repair),
            )
            if result.status != Status.SUCCESS:
                error = failure(result.status, submitted=result.submitted)
                return Result(
                    error.body,
                    error.http_status,
                    result.status,
                    result.submitted,
                    error.retry_safe,
                    result.reset_at,
                )
            try:
                return parse(result.text, body, self.name, result.observed_model)
            except GatewayError:
                if repair:
                    return failure(Status.INVALID_OUTPUT, submitted=True)
        raise AssertionError("Unreachable")


def registry(transport: BrowserTransport) -> dict[str, BrowserAdapter]:
    return {name: BrowserAdapter(name, transport) for name in ("chatgpt", "claude")}
