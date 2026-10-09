"""WhatsApp notification channel — delivers alerts via the Meta Cloud API."""

from __future__ import annotations

from datetime import UTC

import structlog
from httpx import AsyncClient, HTTPStatusError

from notification.config import (
    DEFAULT_PREDICTION_TEMPLATE,
    DEFAULT_TEMPLATE_LANGUAGE,
    DEFAULT_VERIFICATION_TEMPLATE,
)
from notification.models import NotificationMessage, VerificationMessage

logger = structlog.get_logger(__name__)

_META_API_BASE = "https://graph.facebook.com"


class WhatsAppDeliveryError(RuntimeError):
    """Meta did not accept a message for every configured recipient."""


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
        *,
        prediction_template: str = DEFAULT_PREDICTION_TEMPLATE,
        verification_template: str = DEFAULT_VERIFICATION_TEMPLATE,
        template_language: str = DEFAULT_TEMPLATE_LANGUAGE,
    ) -> None:
        self._access_token = access_token
        self._phone_number_id = phone_number_id
        self._api_version = api_version
        self._recipients = recipients
        self._http = http_client
        self._prediction_template = prediction_template
        self._verification_template = verification_template
        self._template_language = template_language
        self._url = f"{_META_API_BASE}/{api_version}/{phone_number_id}/messages"

    async def send(self, message: NotificationMessage) -> None:
        await self._send_template(
            self._prediction_template,
            [
                f"{message.company_name} ({message.exchange}: {message.ticker})",
                message.direction,
                message.signal_strength,
                f"{message.confidence * 100:.1f}%",
                message.decided_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S"),
            ],
        )

    async def send_scored(self, message: VerificationMessage) -> None:
        await self._send_template(
            self._verification_template,
            [
                f"{message.company_name} ({message.exchange}: {message.ticker})",
                "CORRECT" if message.is_correct else "WRONG",
                f"{message.predicted_direction} / {message.predicted_magnitude}",
                f"{message.actual_direction} / {message.actual_magnitude}",
                f"{message.actual_return * 100:+.2f}%",
                f"{message.confidence * 100:.1f}%",
                message.scored_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S"),
            ],
        )

    async def _send_template(self, name: str, parameters: list[str]) -> None:
        successes = 0
        for recipient_index, phone in enumerate(self._recipients):
            payload = {
                "messaging_product": "whatsapp",
                "to": phone,
                "type": "template",
                "template": {
                    "name": name,
                    "language": {"code": self._template_language},
                    "components": [
                        {
                            "type": "body",
                            "parameters": [{"type": "text", "text": value} for value in parameters],
                        }
                    ],
                },
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
                data = response.json()
                messages = data.get("messages") if isinstance(data, dict) else None
                if (
                    not isinstance(messages, list)
                    or not messages
                    or not isinstance(messages[0], dict)
                    or not isinstance(messages[0].get("id"), str)
                    or not messages[0]["id"].strip()
                ):
                    raise WhatsAppDeliveryError("Meta response contains no message ID")
                successes += 1
            except HTTPStatusError as exc:
                error = {}
                try:
                    body = exc.response.json()
                    if isinstance(body, dict) and isinstance(body.get("error"), dict):
                        error = body["error"]
                except ValueError:
                    pass
                logger.error(
                    "whatsapp_send_failed",
                    recipient_index=recipient_index,
                    status_code=exc.response.status_code,
                    error_code=error.get("code"),
                    error_subcode=error.get("error_subcode"),
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "whatsapp_send_failed",
                    recipient_index=recipient_index,
                    error_type=type(exc).__name__,
                )
        logger.info("whatsapp_channel_done", total=len(self._recipients), successes=successes)
        if successes < len(self._recipients):
            raise WhatsAppDeliveryError(
                f"WhatsApp accepted {successes}/{len(self._recipients)} messages"
            )
