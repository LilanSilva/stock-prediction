"""Notification engine — confidence gate, message construction, and channel dispatch."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import structlog
from shared.reference.asset_registry import resolve
from shared.reference.exceptions import UnknownAssetError
from shared.messaging.exceptions import MessageProcessingError, MessagePoisonError
from shared.schemas.messages import Magnitude, PredictionMade

from notification.channels.base import NotificationChannel
from notification.models import NotificationMessage

if TYPE_CHECKING:
    pass

logger = structlog.get_logger(__name__)

_MAGNITUDE_TO_SIGNAL: dict[str, str] = {
    Magnitude.LARGE.value: "HIGH",
    Magnitude.MEDIUM.value: "MEDIUM",
    Magnitude.SMALL.value: "LOW",
}


class NotificationEngine:
    """Processes a PredictionMade message: gate → build → dispatch to all channels."""

    def __init__(
        self,
        channels: list[NotificationChannel],
        min_confidence: float,
        channel_timeout_seconds: float,
    ) -> None:
        self._channels = channels
        self._min_confidence = min_confidence
        self._channel_timeout = channel_timeout_seconds

    async def handle(self, prediction: PredictionMade) -> None:
        log = logger.bind(
            prediction_id=str(prediction.prediction_id),
            asset_id=str(prediction.asset_id),
            correlation_id=str(prediction.correlation_id),
        )

        if prediction.confidence < self._min_confidence:
            log.info(
                "prediction_below_threshold",
                confidence=prediction.confidence,
                threshold=self._min_confidence,
            )
            return

        try:
            asset = resolve(str(prediction.asset_id))
        except UnknownAssetError as exc:
            raise MessageProcessingError(f"unknown asset_id: {prediction.asset_id}") from exc

        signal_strength = _MAGNITUDE_TO_SIGNAL.get(prediction.magnitude.value, prediction.magnitude.value)

        message = NotificationMessage(
            company_name=asset.display_name,
            exchange=asset.expected_exchange,
            ticker=asset.provider_symbol,
            direction=prediction.direction.value,
            signal_strength=signal_strength,
            confidence=prediction.confidence,
            decided_at=prediction.decision_at,
        )

        await self._dispatch(message, log)

    async def _dispatch(
        self, message: NotificationMessage, log: structlog.BoundLogger
    ) -> None:
        if not self._channels:
            log.warning("no_channels_registered")
            return

        async def _call_channel(channel: NotificationChannel) -> None:
            try:
                await asyncio.wait_for(
                    channel.send(message), timeout=self._channel_timeout
                )
                log.info(
                    "channel_dispatch_succeeded",
                    channel_id=channel.channel_id,
                )
            except asyncio.TimeoutError:
                log.error("channel_dispatch_timeout", channel_id=channel.channel_id)
                raise
            except Exception as exc:
                log.error(
                    "channel_dispatch_failed",
                    channel_id=channel.channel_id,
                    error=str(exc),
                )
                raise

        results = await asyncio.gather(
            *[_call_channel(ch) for ch in self._channels],
            return_exceptions=True,
        )

        failures = [r for r in results if isinstance(r, BaseException)]
        if len(failures) == len(self._channels):
            raise MessageProcessingError(
                f"all {len(self._channels)} channel(s) failed to dispatch notification"
            )
