"""Email notification channel — delivers alerts via the Brevo transactional email API."""

from __future__ import annotations

import structlog
from httpx import AsyncClient

from notification.models import NotificationMessage

logger = structlog.get_logger(__name__)

_BREVO_SEND_URL = "https://api.brevo.com/v3/smtp/email"


class EmailChannel:
    """Sends one email per recipient using the Brevo API."""

    channel_id = "email"

    def __init__(
        self,
        api_key: str,
        sender_email: str,
        sender_name: str,
        recipients: list[str],
        http_client: AsyncClient,
    ) -> None:
        self._api_key = api_key
        self._sender_email = sender_email
        self._sender_name = sender_name
        self._recipients = recipients
        self._http = http_client

    async def send(self, message: NotificationMessage) -> None:
        subject = _build_subject(message)
        body = _build_body(message)
        successes = 0
        for address in self._recipients:
            payload = {
                "sender": {"name": self._sender_name, "email": self._sender_email},
                "to": [{"email": address}],
                "subject": subject,
                "textContent": body,
            }
            try:
                response = await self._http.post(
                    _BREVO_SEND_URL,
                    json=payload,
                    headers={"api-key": self._api_key, "Content-Type": "application/json"},
                )
                response.raise_for_status()
                successes += 1
            except Exception as exc:  # noqa: BLE001
                logger.error("email_send_failed", recipient=address, error=str(exc))
        logger.info("email_channel_done", total=len(self._recipients), successes=successes)


def _build_subject(msg: NotificationMessage) -> str:
    return (
        f"[FEED ANALYZER] {msg.company_name} ({msg.exchange}: {msg.ticker})"
        f" — {msg.direction} / {msg.signal_strength}"
    )


def _build_body(msg: NotificationMessage) -> str:
    confidence_pct = round(msg.confidence * 100, 1)
    decided_str = msg.decided_at.strftime("%Y-%m-%d %H:%M:%S")
    body = (
        "Feed Analyzer — Prediction Alert\n"
        "\n"
        f"Company   : {msg.company_name} ({msg.exchange}: {msg.ticker})\n"
        f"Direction : {msg.direction}\n"
        f"Signal    : {msg.signal_strength}\n"
        f"Confidence: {confidence_pct}%\n"
        f"Decided   : {decided_str} UTC\n"
    )
    if msg.headlines:
        lines = "\n".join(f"  • {h.title} [{h.source_id}]" for h in msg.headlines)
        body += f"\nTop News:\n{lines}\n"
    return body
