"""Unit tests for WhatsAppChannel — all HTTP calls are intercepted with httpx.MockTransport."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from notification.channels.whatsapp_channel import WhatsAppChannel, _build_scored_text, _build_text
from notification.models import Headline, NotificationMessage, VerificationMessage

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
# Message builder tests (pure, no HTTP)
# ---------------------------------------------------------------------------


def test_build_text_contains_required_fields() -> None:
    msg = _alert_message()
    text = _build_text(msg)
    assert "Ericsson" in text
    assert "NASDAQ" in text
    assert "ERIC" in text
    assert "UP" in text
    assert "HIGH" in text
    assert "87.0%" in text
    assert "2026-08-12 14:30:00 UTC" in text


def test_build_text_includes_headlines() -> None:
    headlines = [
        Headline(title="Ericsson wins 5G deal", source_id="reuters"),
        Headline(title="ERIC jumps 4%", source_id="bloomberg"),
    ]
    text = _build_text(_alert_message(headlines=headlines))
    assert "Ericsson wins 5G deal [reuters]" in text
    assert "ERIC jumps 4% [bloomberg]" in text


def test_build_text_no_headlines_omits_section() -> None:
    text = _build_text(_alert_message(headlines=[]))
    assert "Top News" not in text


def test_build_scored_text_correct_outcome() -> None:
    text = _build_scored_text(_verification_message(is_correct=True))
    assert "CORRECT" in text
    assert "Ericsson" in text
    assert "+3.12%" in text
    assert "2026-08-12 16:00:00 UTC" in text


def test_build_scored_text_wrong_outcome() -> None:
    text = _build_scored_text(_verification_message(is_correct=False))
    assert "WRONG" in text


# ---------------------------------------------------------------------------
# HTTP dispatch tests (mocked transport)
# ---------------------------------------------------------------------------


def _make_channel(handler: httpx.MockTransport) -> WhatsAppChannel:
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
    await channel.send(_alert_message())

    body = json.loads(transport.requests[0].content)
    assert body["messaging_product"] == "whatsapp"
    assert body["to"] == _RECIPIENT
    assert body["type"] == "text"
    assert "body" in body["text"]
    assert "Ericsson" in body["text"]["body"]


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


async def test_send_scored_posts_to_meta_api() -> None:
    transport = _CapturingTransport()
    channel = _make_channel(transport)
    await channel.send_scored(_verification_message())

    assert len(transport.requests) == 1
    body = json.loads(transport.requests[0].content)
    assert body["messaging_product"] == "whatsapp"
    assert "CORRECT" in body["text"]["body"]
    assert "Ericsson" in body["text"]["body"]


async def test_send_does_not_raise_on_api_error() -> None:
    """A failed API call is logged but must not propagate — other recipients still get notified."""

    class _FailTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"error": {"message": "Invalid token"}})

    channel = _make_channel(_FailTransport())
    # Should complete without raising, even though the API returned 400.
    await channel.send(_alert_message())


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
