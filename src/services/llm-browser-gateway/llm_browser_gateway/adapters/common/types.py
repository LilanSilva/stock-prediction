from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

type JSON = dict[str, Any]


class Status(StrEnum):
    SUCCESS = "success"
    RATE_LIMITED = "rate_limited"
    LOGIN_REQUIRED = "login_required"
    VERIFICATION_REQUIRED = "verification_required"
    DISCONNECTED = "browser_disconnected"
    UI_CHANGED = "ui_changed"
    TEMPORARY = "temporary_unavailable"
    INVALID_OUTPUT = "invalid_output"
    UNKNOWN = "submission_unknown"
    INVALID_REQUEST = "invalid_request"
    REFUSED = "refused"


@dataclass(frozen=True)
class Context:
    request_id: str
    deadline: float
    profile_id: str
    required: frozenset[str] = field(default_factory=frozenset)
    slot: int = 0


@dataclass(frozen=True)
class Result:
    body: JSON
    http_status: int
    status: Status
    submitted: bool | None = False
    retry_safe: bool = True
    reset_at: datetime | None = None


@dataclass(frozen=True)
class BrowserResult:
    status: Status
    text: str = ""
    submitted: bool | None = False
    reset_at: datetime | None = None
    observed_model: str | None = None


class GatewayError(Exception):
    def __init__(
        self,
        message: str,
        status: int = 400,
        code: str = "invalid_request",
        param: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.http_status = status
        self.code = code
        self.param = param


def error_body(message: str, code: str, param: str | None = None) -> JSON:
    return {"error": {"message": message, "type": code, "param": param, "code": code}}


def failure(status: Status, *, submitted: bool | None = False) -> Result:
    http = {
        Status.RATE_LIMITED: 429,
        Status.INVALID_REQUEST: 400,
        Status.INVALID_OUTPUT: 502,
        Status.UNKNOWN: 503,
    }.get(status, 503)
    return Result(
        error_body(f"Browser adapter reported {status.value}", status.value),
        http,
        status,
        submitted=submitted,
        retry_safe=submitted is not None and status != Status.UNKNOWN,
    )
