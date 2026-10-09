import uuid

from shared.logging import get_logger

from .base import BrowserTransport, remaining
from .common.contract import CAPABILITIES
from .common.formatting import parse, render
from .common.types import JSON, BrowserResult, Context, GatewayError, Result, Status, failure

LOG = get_logger(__name__)


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
            except GatewayError as exc:
                LOG.warning(
                    "browser_output_rejected",
                    provider=self.name,
                    attempt_id=attempt_id,
                    repair=repair,
                    output_chars=len(result.text),
                    cause=type(exc.__cause__).__name__,
                )
                if repair:
                    return failure(Status.INVALID_OUTPUT, submitted=True)
        raise AssertionError("Unreachable")


def registry(transport: BrowserTransport) -> dict[str, BrowserAdapter]:
    return {
        name: BrowserAdapter(name, transport)
        for name in ("chatgpt", "claude", "deepseek", "meta", "kimi", "gemini")
    }
