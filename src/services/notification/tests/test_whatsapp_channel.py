"""Unit tests for WhatsAppChannel — all HTTP calls are intercepted with httpx.MockTransport."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import httpx
import pytest
import structlog
from notification.app import _build_channels
from notification.channels.whatsapp_channel import (
    WhatsAppChannel,
    WhatsAppDeliveryError,
)
from notification.config import NotificationSettings
from notification.engine import NotificationEngine
from notification.models import Headline, NotificationMessage, VerificationMessage
from pydantic import ValidationError
from shared.messaging.exceptions import MessageProcessingError

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_TOKEN = "test-token"
_PHONE_NUMBER_ID = "123456789"
_API_VERSION = "v18.0"
_RECIPIENT = "+94774029169"
_EXPECTED_URL = f"https://graph.facebook.com/{_API_VERSION}/{_PHONE_NUMBER_ID}/messages"


def _alert_message(*, headlines: list[Headline] | None = None) -> NotificationMessage:
    return NotificationMessage(
        company_name="Ericsson",
        exchange="NASDAQ",
        ticker="ERIC",
        direction="UP",
        signal_strength="HIGH",
        confidence=0.87,
        decided_at=datetime(2026, 8, 12, 14, 30, 0, tzinfo=UTC),
        headlines=headlines or [],
    )


def _verification_message(*, is_correct: bool = True) -> VerificationMessage:
    return VerificationMessage(
        company_name="Ericsson",
        exchange="NASDAQ",
        ticker="ERIC",
        predicted_direction="UP",
        actual_direction="UP",
        predicted_magnitude="LARGE",
        actual_magnitude="LARGE",
        actual_return=0.0312,
        confidence=0.87,
        is_correct=is_correct,
        scored_at=datetime(2026, 8, 12, 16, 0, 0, tzinfo=UTC),
    )


# ---------------------------------------------------------------------------
# HTTP dispatch tests (mocked transport)
# ---------------------------------------------------------------------------


def _make_channel(handler: httpx.AsyncBaseTransport) -> WhatsAppChannel:
    client = httpx.AsyncClient(transport=handler)
    return WhatsAppChannel(
        access_token=_TOKEN,
        phone_number_id=_PHONE_NUMBER_ID,
        api_version=_API_VERSION,
        recipients=[_RECIPIENT],
        http_client=client,
    )


class _CapturingTransport(httpx.AsyncBaseTransport):
    """Records all requests made through the transport and returns 200."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.test"}]})


async def test_send_posts_to_meta_api() -> None:
    transport = _CapturingTransport()
    channel = _make_channel(transport)
    await channel.send(_alert_message())

    assert len(transport.requests) == 1
    req = transport.requests[0]
    assert str(req.url) == _EXPECTED_URL
    assert req.method == "POST"


async def test_send_uses_bearer_token() -> None:
    transport = _CapturingTransport()
    channel = _make_channel(transport)
    await channel.send(_alert_message())

    auth = transport.requests[0].headers["authorization"]
    assert auth == f"Bearer {_TOKEN}"


async def test_send_payload_structure() -> None:
    transport = _CapturingTransport()
    channel = _make_channel(transport)
    await channel.send(
        _alert_message(
            headlines=[
                Headline(title="Not part of the approved template", source_id="test"),
            ]
        )
    )

    body = json.loads(transport.requests[0].content)
    assert body["messaging_product"] == "whatsapp"
    assert body["to"] == _RECIPIENT
    assert body["type"] == "template"
    assert "text" not in body
    assert body["template"] == {
        "name": "feed_prediction_alert_v1",
        "language": {"code": "en_US"},
        "components": [
            {
                "type": "body",
                "parameters": [
                    {"type": "text", "text": "Ericsson (NASDAQ: ERIC)"},
                    {"type": "text", "text": "UP"},
                    {"type": "text", "text": "HIGH"},
                    {"type": "text", "text": "87.0%"},
                    {"type": "text", "text": "2026-08-12 14:30:00"},
                ],
            }
        ],
    }


async def test_send_one_request_per_recipient() -> None:
    transport = _CapturingTransport()
    client = httpx.AsyncClient(transport=transport)
    channel = WhatsAppChannel(
        access_token=_TOKEN,
        phone_number_id=_PHONE_NUMBER_ID,
        api_version=_API_VERSION,
        recipients=["+11111111111", "+22222222222", "+33333333333"],
        http_client=client,
    )
    await channel.send(_alert_message())
    assert len(transport.requests) == 3
    recipients_called = [json.loads(r.content)["to"] for r in transport.requests]
    assert recipients_called == ["+11111111111", "+22222222222", "+33333333333"]


@pytest.mark.parametrize("is_correct", [True, False])
async def test_send_scored_posts_to_meta_api(is_correct: bool) -> None:
    transport = _CapturingTransport()
    channel = _make_channel(transport)
    await channel.send_scored(_verification_message(is_correct=is_correct))

    assert len(transport.requests) == 1
    body = json.loads(transport.requests[0].content)
    assert body["messaging_product"] == "whatsapp"
    assert body["to"] == _RECIPIENT
    assert body["type"] == "template"
    assert body["template"] == {
        "name": "feed_verification_alert_v1",
        "language": {"code": "en_US"},
        "components": [
            {
                "type": "body",
                "parameters": [
                    {"type": "text", "text": "Ericsson (NASDAQ: ERIC)"},
                    {"type": "text", "text": "CORRECT" if is_correct else "WRONG"},
                    {"type": "text", "text": "UP / LARGE"},
                    {"type": "text", "text": "UP / LARGE"},
                    {"type": "text", "text": "+3.12%"},
                    {"type": "text", "text": "87.0%"},
                    {"type": "text", "text": "2026-08-12 16:00:00"},
                ],
            }
        ],
    }


async def test_template_values_convert_times_to_utc_and_preserve_negative_returns() -> None:
    transport = _CapturingTransport()
    channel = _make_channel(transport)
    local_time = datetime(2026, 8, 12, 18, 0, tzinfo=timezone(timedelta(hours=2)))
    await channel.send(replace(_alert_message(), decided_at=local_time))
    await channel.send_scored(
        replace(
            _verification_message(is_correct=False),
            scored_at=local_time,
            actual_return=-0.0312,
            actual_direction="DOWN",
            actual_magnitude="SMALL",
        )
    )
    prediction = json.loads(transport.requests[0].content)["template"]["components"][0]
    scored = json.loads(transport.requests[1].content)["template"]["components"][0]
    assert prediction["parameters"][-1]["text"] == "2026-08-12 16:00:00"
    assert [p["text"] for p in scored["parameters"]] == [
        "Ericsson (NASDAQ: ERIC)",
        "WRONG",
        "UP / LARGE",
        "DOWN / SMALL",
        "-3.12%",
        "87.0%",
        "2026-08-12 16:00:00",
    ]


@pytest.mark.parametrize("scored", [False, True])
async def test_template_rejection_never_falls_back_to_free_text(scored: bool) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(400, json={"error": {"code": 132001}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        channel = WhatsAppChannel(_TOKEN, _PHONE_NUMBER_ID, _API_VERSION, [_RECIPIENT], client)
        with pytest.raises(WhatsAppDeliveryError, match="accepted 0/1"):
            if scored:
                await channel.send_scored(_verification_message())
            else:
                await channel.send(_alert_message())
    assert len(requests) == 1
    assert json.loads(requests[0].content)["type"] == "template"


@pytest.mark.parametrize("custom", [False, True])
async def test_service_wires_template_environment_settings(
    monkeypatch: pytest.MonkeyPatch,
    custom: bool,
) -> None:
    variables = {
        "META_PREDICTION_TEMPLATE": "prediction_v2",
        "META_VERIFICATION_TEMPLATE": "verification_v2",
        "META_TEMPLATE_LANGUAGE": "en_GB",
    }
    for key, value in variables.items():
        if custom:
            monkeypatch.setenv(key, value)
        else:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("notification.app.load_whatsapp_recipients", lambda _: [_RECIPIENT])
    settings = NotificationSettings.model_validate(
        {
            "RABBITMQ_URL": "amqp://localhost/",
            "META_ACCESS_TOKEN": _TOKEN,
            "META_PHONE_NUMBER_ID": _PHONE_NUMBER_ID,
            "BREVO_API_KEY": "",
        }
    )
    transport = _CapturingTransport()
    async with httpx.AsyncClient(transport=transport) as client:
        channels = _build_channels(settings, client)
        assert len(channels) == 1
        await channels[0].send(_alert_message())
        await channels[0].send_scored(_verification_message())
    templates = [json.loads(r.content)["template"] for r in transport.requests]
    assert [t["name"] for t in templates] == (
        ["prediction_v2", "verification_v2"]
        if custom
        else ["feed_prediction_alert_v1", "feed_verification_alert_v1"]
    )
    assert all(t["language"]["code"] == ("en_GB" if custom else "en_US") for t in templates)


@pytest.mark.parametrize(
    "key",
    [
        "META_PREDICTION_TEMPLATE",
        "META_VERIFICATION_TEMPLATE",
        "META_TEMPLATE_LANGUAGE",
    ],
)
def test_empty_template_configuration_is_rejected(key: str) -> None:
    with pytest.raises(ValidationError):
        NotificationSettings.model_validate({"RABBITMQ_URL": "amqp://localhost/", key: ""})


@pytest.mark.parametrize("scored", [False, True])
async def test_send_raises_on_api_error(scored: bool) -> None:
    """An API failure must reach the caller for both alert types."""

    class _FailTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"error": {"message": "Invalid token"}})

    channel = _make_channel(_FailTransport())
    with pytest.raises(WhatsAppDeliveryError, match="accepted 0/1"):
        if scored:
            await channel.send_scored(_verification_message())
        else:
            await channel.send(_alert_message())


@pytest.mark.parametrize("scored", [False, True])
async def test_partial_failure_attempts_remaining_recipients(scored: bool) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(400, json={"error": {"code": 190}})
        return httpx.Response(200, json={"messages": [{"id": "wamid.accepted"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        channel = WhatsAppChannel(
            _TOKEN,
            _PHONE_NUMBER_ID,
            _API_VERSION,
            ["+11111111111", "+22222222222"],
            client,
        )
        with pytest.raises(WhatsAppDeliveryError, match="accepted 1/2"):
            if scored:
                await channel.send_scored(_verification_message())
            else:
                await channel.send(_alert_message())
    assert len(requests) == 2


@pytest.mark.parametrize("failure", [httpx.ConnectError, httpx.ReadTimeout])
async def test_transport_failure_is_not_success(failure: type[httpx.RequestError]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise failure("TLS or connection failure", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        channel = WhatsAppChannel(_TOKEN, _PHONE_NUMBER_ID, _API_VERSION, [_RECIPIENT], client)
        with pytest.raises(WhatsAppDeliveryError, match="accepted 0/1"):
            await channel.send(_alert_message())


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"messages": []},
        {"messages": [{}]},
        {"messages": [{"id": ""}]},
        {"messages": [{"id": 123}]},
        [],
    ],
)
async def test_success_requires_meta_message_id(body: object) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        channel = WhatsAppChannel(_TOKEN, _PHONE_NUMBER_ID, _API_VERSION, [_RECIPIENT], client)
        with pytest.raises(WhatsAppDeliveryError, match="accepted 0/1"):
            await channel.send(_alert_message())


async def test_non_json_error_is_reported_without_secrets(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text=f"sensitive {_TOKEN} {_RECIPIENT}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        channel = WhatsAppChannel(_TOKEN, _PHONE_NUMBER_ID, _API_VERSION, [_RECIPIENT], client)
        with pytest.raises(WhatsAppDeliveryError):
            await channel.send(_alert_message())
    output = capsys.readouterr().out
    assert "status_code=502" in output
    assert _TOKEN not in output
    assert _RECIPIENT not in output


async def test_send_preserves_cancellation() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise asyncio.CancelledError

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        channel = WhatsAppChannel(_TOKEN, _PHONE_NUMBER_ID, _API_VERSION, [_RECIPIENT], client)
        with pytest.raises(asyncio.CancelledError):
            await channel.send(_alert_message())


@pytest.mark.parametrize("scored", [False, True])
async def test_failed_whatsapp_dispatch_reaches_engine_retry_path(scored: bool) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"code": 190}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        channel = WhatsAppChannel(_TOKEN, _PHONE_NUMBER_ID, _API_VERSION, [_RECIPIENT], client)
        engine = NotificationEngine([channel], min_confidence=0.6, channel_timeout_seconds=1)
        with pytest.raises(MessageProcessingError, match="all 1 channel"):
            if scored:
                await engine._dispatch_scored(_verification_message(), structlog.get_logger())
            else:
                await engine._dispatch(_alert_message(), structlog.get_logger())


async def test_send_empty_recipients_sends_nothing() -> None:
    transport = _CapturingTransport()
    client = httpx.AsyncClient(transport=transport)
    channel = WhatsAppChannel(
        access_token=_TOKEN,
        phone_number_id=_PHONE_NUMBER_ID,
        api_version=_API_VERSION,
        recipients=[],
        http_client=client,
    )
    await channel.send(_alert_message())
    assert len(transport.requests) == 0
