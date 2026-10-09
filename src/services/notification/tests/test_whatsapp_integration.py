"""Integration tests — require Meta to accept real messages using infra/.env credentials.

Run explicitly:
    pytest src/services/notification/tests/test_whatsapp_integration.py -v -m integration

Exclude real sends with ``-m 'not integration'``. Missing credentials skip the tests.
Acceptance by Meta does not prove delivery to the recipient's phone.
Both configured templates must be approved in the sender's WhatsApp Business Account.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from notification.channels.whatsapp_channel import WhatsAppChannel
from notification.config import (
    DEFAULT_PREDICTION_TEMPLATE,
    DEFAULT_TEMPLATE_LANGUAGE,
    DEFAULT_VERIFICATION_TEMPLATE,
)
from notification.http_client import create_http_client
from notification.models import Headline, NotificationMessage, VerificationMessage

# ---------------------------------------------------------------------------
# Load credentials from infra/.env at import time so the skip decision is
# available to pytest before the test body runs.
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[4]  # notification/tests -> repo root
_ENV_PATH = _REPO_ROOT / "infra" / ".env"
_RECIPIENTS_PATH = (
    Path(__file__).resolve().parents[1] / "config" / "recipients" / "whatsapp_recipients.json"
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
        val = stripped[idx + 1 :].strip().strip('"')
        env[key] = val
    return env


_ENV = _load_env()
_ACCESS_TOKEN = _ENV.get("META_ACCESS_TOKEN", "")
_PHONE_NUMBER_ID = _ENV.get("META_PHONE_NUMBER_ID", "")
_API_VERSION = _ENV.get("META_API_VERSION", "v18.0")
_PREDICTION_TEMPLATE = _ENV.get("META_PREDICTION_TEMPLATE", DEFAULT_PREDICTION_TEMPLATE)
_VERIFICATION_TEMPLATE = _ENV.get("META_VERIFICATION_TEMPLATE", DEFAULT_VERIFICATION_TEMPLATE)
_TEMPLATE_LANGUAGE = _ENV.get("META_TEMPLATE_LANGUAGE", DEFAULT_TEMPLATE_LANGUAGE)
_RECIPIENTS: list[str] = (
    json.loads(_RECIPIENTS_PATH.read_text(encoding="utf-8")) if _RECIPIENTS_PATH.exists() else []
)

_credentials_missing = not _ACCESS_TOKEN or not _PHONE_NUMBER_ID or not _RECIPIENTS
_skip_reason = (
    "META_ACCESS_TOKEN / META_PHONE_NUMBER_ID not set in infra/.env or no recipients configured"
)


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(_credentials_missing, reason=_skip_reason)
async def test_send_alert_message_accepted_by_meta() -> None:
    """Sends a prediction alert to every recipient in whatsapp_recipients.json."""
    message = NotificationMessage(
        company_name="Ericsson (integration test)",
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
    async with create_http_client() as client:
        channel = WhatsAppChannel(
            access_token=_ACCESS_TOKEN,
            phone_number_id=_PHONE_NUMBER_ID,
            api_version=_API_VERSION,
            recipients=_RECIPIENTS,
            http_client=client,
            prediction_template=_PREDICTION_TEMPLATE,
            verification_template=_VERIFICATION_TEMPLATE,
            template_language=_TEMPLATE_LANGUAGE,
        )
        # The channel raises unless Meta returns a message ID for every recipient.
        await channel.send(message)


@pytest.mark.integration
@pytest.mark.skipif(_credentials_missing, reason=_skip_reason)
async def test_send_verification_message_accepted_by_meta() -> None:
    """Sends a verification/scored alert to every recipient in whatsapp_recipients.json."""
    message = VerificationMessage(
        company_name="Ericsson (integration test)",
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
    async with create_http_client() as client:
        channel = WhatsAppChannel(
            access_token=_ACCESS_TOKEN,
            phone_number_id=_PHONE_NUMBER_ID,
            api_version=_API_VERSION,
            recipients=_RECIPIENTS,
            http_client=client,
            prediction_template=_PREDICTION_TEMPLATE,
            verification_template=_VERIFICATION_TEMPLATE,
            template_language=_TEMPLATE_LANGUAGE,
        )
        await channel.send_scored(message)
