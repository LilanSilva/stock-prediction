"""Notification engine — confidence gate, message construction, and channel dispatch."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import asyncpg
import structlog
from shared.messaging.exceptions import MessageProcessingError
from shared.reference.asset_registry import resolve
from shared.reference.exceptions import UnknownAssetError
from shared.schemas.messages import Magnitude, PredictionMade, PredictionScored

from notification.channels.base import NotificationChannel
from notification.db import fetch_headlines
from notification.models import Headline, NotificationMessage, VerificationMessage

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
        db_pool: asyncpg.Pool | None = None,
    ) -> None:
        self._channels = channels
        self._min_confidence = min_confidence
        self._channel_timeout = channel_timeout_seconds
        self._db_pool = db_pool

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

        headlines: list[Headline] = []
        if self._db_pool is not None:
            try:
                rows = await fetch_headlines(self._db_pool, list(prediction.event_ids))
                headlines = [Headline(title=t, source_id=s) for t, s in rows]
            except Exception as exc:  # noqa: BLE001
                log.warning("headlines_fetch_failed", error=str(exc))

        message = NotificationMessage(
            company_name=asset.display_name,
            exchange=asset.expected_exchange,
            ticker=asset.provider_symbol,
            direction=prediction.direction.value,
            signal_strength=signal_strength,
            confidence=prediction.confidence,
            decided_at=prediction.decision_at,
            headlines=headlines,
        )

        await self._dispatch(message, log)

    async def handle_scored(self, scored: PredictionScored) -> None:
        log = logger.bind(
            prediction_id=str(scored.prediction_id),
            asset_id=str(scored.asset_id),
            correlation_id=str(scored.correlation_id),
        )

        try:
            asset = resolve(str(scored.asset_id))
        except UnknownAssetError as exc:
            raise MessageProcessingError(f"unknown asset_id: {scored.asset_id}") from exc

        message = VerificationMessage(
            company_name=asset.display_name,
            exchange=asset.expected_exchange,
            ticker=asset.provider_symbol,
            predicted_direction=scored.predicted_direction.value,
            actual_direction=scored.actual_direction.value,
            predicted_magnitude=scored.predicted_magnitude.value,
            actual_magnitude=scored.actual_magnitude.value,
            actual_return=scored.actual_return,
            confidence=scored.confidence,
            is_correct=scored.is_correct,
            scored_at=scored.scored_at,
        )

        await self._dispatch_scored(message, log)

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

    async def _dispatch_scored(
        self, message: VerificationMessage, log: structlog.BoundLogger
    ) -> None:
        if not self._channels:
            log.warning("no_channels_registered")
            return

        async def _call_channel(channel: NotificationChannel) -> None:
            try:
                await asyncio.wait_for(
                    channel.send_scored(message), timeout=self._channel_timeout
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
                f"all {len(self._channels)} channel(s) failed to dispatch verification notification"
            )
