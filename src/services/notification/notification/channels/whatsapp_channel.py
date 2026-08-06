"""WhatsApp notification channel — delivers alerts via the Meta Cloud API."""

from __future__ import annotations

import structlog
from httpx import AsyncClient

from notification.models import NotificationMessage, VerificationMessage

logger = structlog.get_logger(__name__)

_META_API_BASE = "https://graph.facebook.com"


class WhatsAppChannel:
    """Sends one WhatsApp message per recipient using the Meta Cloud API."""

    channel_id = "whatsapp"

    def __init__(
        self,
        access_token: str,
        phone_number_id: str,
        api_version: str,
        recipients: list[str],
        http_client: AsyncClient,
    ) -> None:
        self._access_token = access_token
        self._phone_number_id = phone_number_id
        self._api_version = api_version
        self._recipients = recipients
        self._http = http_client
        self._url = f"{_META_API_BASE}/{api_version}/{phone_number_id}/messages"

    async def send(self, message: NotificationMessage) -> None:
        text = _build_text(message)
        successes = 0
        for phone in self._recipients:
            payload = {
                "messaging_product": "whatsapp",
                "to": phone,
                "type": "text",
                "text": {"body": text},
            }
            try:
                response = await self._http.post(
                    self._url,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self._access_token}",
                        "Content-Type": "application/json",
                    },
                )
                response.raise_for_status()
                successes += 1
            except Exception as exc:  # noqa: BLE001
                logger.error("whatsapp_send_failed", recipient=phone, error=str(exc))
        logger.info("whatsapp_channel_done", total=len(self._recipients), successes=successes)


    async def send_scored(self, message: VerificationMessage) -> None:
        text = _build_scored_text(message)
        successes = 0
        for phone in self._recipients:
            payload = {
                "messaging_product": "whatsapp",
                "to": phone,
                "type": "text",
                "text": {"body": text},
            }
            try:
                response = await self._http.post(
                    self._url,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self._access_token}",
                        "Content-Type": "application/json",
                    },
                )
                response.raise_for_status()
                successes += 1
            except Exception as exc:  # noqa: BLE001
                logger.error("whatsapp_send_failed", recipient=phone, error=str(exc))
        logger.info("whatsapp_channel_done", total=len(self._recipients), successes=successes)


def _build_text(msg: NotificationMessage) -> str:
    confidence_pct = round(msg.confidence * 100, 1)
    decided_str = msg.decided_at.strftime("%Y-%m-%d %H:%M:%S")
    text = (
        "Feed Analyzer Alert\n"
        f"{msg.company_name} ({msg.exchange}: {msg.ticker})\n"
        f"Direction : {msg.direction}\n"
        f"Signal    : {msg.signal_strength}\n"
        f"Confidence: {confidence_pct}%\n"
        f"Decided   : {decided_str} UTC"
    )
    if msg.headlines:
        lines = "\n".join(f"• {h.title} [{h.source_id}]" for h in msg.headlines)
        text += f"\n\nTop News:\n{lines}"
    return text


def _build_scored_text(msg: VerificationMessage) -> str:
    confidence_pct = round(msg.confidence * 100, 1)
    scored_str = msg.scored_at.strftime("%Y-%m-%d %H:%M:%S")
    actual_return_pct = round(msg.actual_return * 100, 2)
    outcome = "CORRECT" if msg.is_correct else "WRONG"
    return (
        f"Feed Analyzer — Verification Alert\n"
        f"{msg.company_name} ({msg.exchange}: {msg.ticker})\n"
        f"Outcome    : {outcome}\n"
        f"Predicted  : {msg.predicted_direction} / {msg.predicted_magnitude}\n"
        f"Actual     : {msg.actual_direction} / {msg.actual_magnitude}\n"
        f"Return     : {actual_return_pct:+.2f}%\n"
        f"Confidence : {confidence_pct}%\n"
        f"Scored     : {scored_str} UTC"
    )
