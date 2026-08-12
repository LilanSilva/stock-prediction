"""Integration test — sends a real WhatsApp message using credentials from infra/.env.

Run explicitly:
    pytest src/services/notification/tests/test_whatsapp_integration.py -v -m integration

Skipped automatically in normal test runs (no META_ACCESS_TOKEN configured or --no-integration).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from notification.channels.whatsapp_channel import WhatsAppChannel
from notification.models import Headline, NotificationMessage, VerificationMessage

# ---------------------------------------------------------------------------
# Load credentials from infra/.env at import time so the skip decision is
# available to pytest before the test body runs.
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[4]   # notification/tests -> repo root
_ENV_PATH = _REPO_ROOT / "infra" / ".env"
_RECIPIENTS_PATH = (
    Path(__file__).resolve().parents[1]
    / "config" / "recipients" / "whatsapp_recipients.json"
)


def _load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    if not _ENV_PATH.exists():
        return env
    for line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        idx = stripped.index("=") if "=" in stripped else -1
        if idx < 1:
            continue
        key = stripped[:idx].strip()
        val = stripped[idx + 1:].strip().strip('"')
        env[key] = val
    return env


_ENV = _load_env()
_ACCESS_TOKEN = _ENV.get("META_ACCESS_TOKEN", "")
_PHONE_NUMBER_ID = _ENV.get("META_PHONE_NUMBER_ID", "")
_API_VERSION = _ENV.get("META_API_VERSION", "v18.0")
_RECIPIENTS: list[str] = (
    json.loads(_RECIPIENTS_PATH.read_text(encoding="utf-8"))
    if _RECIPIENTS_PATH.exists()
    else []
)

_credentials_missing = not _ACCESS_TOKEN or not _PHONE_NUMBER_ID or not _RECIPIENTS
_skip_reason = "META_ACCESS_TOKEN / META_PHONE_NUMBER_ID not set in infra/.env or no recipients configured"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _channel() -> WhatsAppChannel:
    return WhatsAppChannel(
        access_token=_ACCESS_TOKEN,
        phone_number_id=_PHONE_NUMBER_ID,
        api_version=_API_VERSION,
        recipients=_RECIPIENTS,
        http_client=httpx.AsyncClient(),
    )


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.skipif(_credentials_missing, reason=_skip_reason)
async def test_send_alert_message_reaches_whatsapp() -> None:
    """Sends a prediction alert to every recipient in whatsapp_recipients.json."""
    message = NotificationMessage(
        company_name="Ericsson",
        exchange="NASDAQ",
        ticker="ERIC",
        direction="UP",
        signal_strength="HIGH",
        confidence=0.87,
        decided_at=datetime.now(UTC),
        headlines=[
            Headline(title="Ericsson wins major 5G contract in Asia", source_id="reuters"),
            Headline(title="ERIC shares rise on strong order book", source_id="bloomberg"),
        ],
    )
    async with httpx.AsyncClient() as client:
        channel = WhatsAppChannel(
            access_token=_ACCESS_TOKEN,
            phone_number_id=_PHONE_NUMBER_ID,
            api_version=_API_VERSION,
            recipients=_RECIPIENTS,
            http_client=client,
        )
        # send() logs errors but does not raise — success means no exception raised
        await channel.send(message)


@pytest.mark.integration
@pytest.mark.skipif(_credentials_missing, reason=_skip_reason)
async def test_send_verification_message_reaches_whatsapp() -> None:
    """Sends a verification/scored alert to every recipient in whatsapp_recipients.json."""
    message = VerificationMessage(
        company_name="Ericsson",
        exchange="NASDAQ",
        ticker="ERIC",
        predicted_direction="UP",
        actual_direction="UP",
        predicted_magnitude="LARGE",
        actual_magnitude="LARGE",
        actual_return=0.0312,
        confidence=0.87,
        is_correct=True,
        scored_at=datetime.now(UTC),
    )
    async with httpx.AsyncClient() as client:
        channel = WhatsAppChannel(
            access_token=_ACCESS_TOKEN,
            phone_number_id=_PHONE_NUMBER_ID,
            api_version=_API_VERSION,
            recipients=_RECIPIENTS,
            http_client=client,
        )
        await channel.send_scored(message)
