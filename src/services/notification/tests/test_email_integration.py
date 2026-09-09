"""Integration test — sends a real email via Brevo using credentials from infra/.env.

Run explicitly:
    pytest src/services/notification/tests/test_email_integration.py -v -m integration

Skipped automatically in normal test runs (no BREVO_API_KEY configured or --no-integration).

Note: each test sends one real email per recipient in ``email_recipients.json`` and consumes
Brevo daily send credits (300/day on the free plan).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from notification.channels.email_channel import EmailChannel
from notification.models import Headline, NotificationMessage, VerificationMessage

# ---------------------------------------------------------------------------
# Load credentials from infra/.env at import time so the skip decision is
# available to pytest before the test body runs.
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[4]   # notification/tests -> repo root
_ENV_PATH = _REPO_ROOT / "infra" / ".env"
_RECIPIENTS_PATH = (
    Path(__file__).resolve().parents[1]
    / "config" / "recipients" / "email_recipients.json"
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


def _load_recipients() -> list[str]:
    """Return configured recipients; an absent or malformed file yields an empty list.

    Mirrors the tolerance of ``channels.recipients._load_strings`` so a bad file skips the
    test instead of failing collection.
    """
    if not _RECIPIENTS_PATH.exists():
        return []
    try:
        data = json.loads(_RECIPIENTS_PATH.read_text(encoding="utf-8"))
    except ValueError:
        return []
    return [str(item) for item in data] if isinstance(data, list) else []


_ENV = _load_env()
_API_KEY = _ENV.get("BREVO_API_KEY", "")
_SENDER_EMAIL = _ENV.get("BREVO_SENDER_EMAIL", "")
_SENDER_NAME = _ENV.get("BREVO_SENDER_NAME", "Feed Analyzer")
_RECIPIENTS: list[str] = _load_recipients()

_credentials_missing = not _API_KEY or not _SENDER_EMAIL or not _RECIPIENTS
_skip_reason = (
    "BREVO_API_KEY / BREVO_SENDER_EMAIL not set in infra/.env or no recipients configured"
)

# Marks the delivered mail as a test so it is not mistaken for a live prediction alert.
_TEST_COMPANY = "Ericsson (integration test)"


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.skipif(_credentials_missing, reason=_skip_reason)
async def test_send_alert_message_reaches_email() -> None:
    """Sends a prediction alert to every recipient in email_recipients.json."""
    message = NotificationMessage(
        company_name=_TEST_COMPANY,
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
        channel = EmailChannel(
            api_key=_API_KEY,
            sender_email=_SENDER_EMAIL,
            sender_name=_SENDER_NAME,
            recipients=_RECIPIENTS,
            http_client=client,
        )
        # send() logs errors but does not raise — success means no exception raised
        await channel.send(message)


@pytest.mark.integration
@pytest.mark.skipif(_credentials_missing, reason=_skip_reason)
async def test_send_verification_message_reaches_email() -> None:
    """Sends a verification/scored alert to every recipient in email_recipients.json."""
    message = VerificationMessage(
        company_name=_TEST_COMPANY,
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
        channel = EmailChannel(
            api_key=_API_KEY,
            sender_email=_SENDER_EMAIL,
            sender_name=_SENDER_NAME,
            recipients=_RECIPIENTS,
            http_client=client,
        )
        await channel.send_scored(message)


@pytest.mark.integration
@pytest.mark.skipif(_credentials_missing, reason=_skip_reason)
async def test_api_key_is_accepted_by_brevo() -> None:
    """Fails fast with a clear signal when the configured API key is rejected."""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://api.brevo.com/v3/account",
            headers={"api-key": _API_KEY},
        )
    assert response.status_code == 200, f"Brevo rejected the API key (HTTP {response.status_code})"


@pytest.mark.integration
@pytest.mark.skipif(_credentials_missing, reason=_skip_reason)
async def test_sender_email_is_verified_in_brevo() -> None:
    """Brevo silently 4xxs every send when the sender is not a verified account sender."""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://api.brevo.com/v3/senders",
            headers={"api-key": _API_KEY},
        )
    assert response.status_code == 200
    senders = {
        str(s.get("email", "")).lower()
        for s in response.json().get("senders", [])
    }
    assert _SENDER_EMAIL.lower() in senders, "BREVO_SENDER_EMAIL is not a verified Brevo sender"
