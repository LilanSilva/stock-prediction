"""Demo publisher: emit one EventDetected (war de-escalation affecting oil transport) to feed.events.

Proves the conditional-causality chain end to end: MILITARY_CONFLICT under TRANSPORT_AFFECTED with
polarity RESOLUTION, against an elevated BRENT_OIL price, must yield a BRENT_OIL DOWN prediction.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import aio_pika
from shared.schemas.messages import (
    ConditionCode,
    EventDetected,
    EventPolarity,
    EventType,
    ExtractionMethod,
    RoutingKey,
)
from shared.schemas.messages import AssetId

EXCHANGE = "feed.events"


async def main() -> None:
    url = os.environ["RABBITMQ_URL"]
    # Backdate so the prediction context window has already elapsed and the close sweep fires soon.
    seen = datetime.now(UTC) - timedelta(hours=6)
    event = EventDetected(
        correlation_id=uuid.uuid4(),
        occurred_at=datetime.now(UTC),
        event_id=uuid.uuid4(),
        cluster_id=uuid.uuid4(),
        canonical_summary="Ceasefire: Iran attack called off, Hormuz oil shipping lanes reopen",
        event_type=EventType.MILITARY_CONFLICT,
        actor="US",
        action="ceasefire",
        object="Iran",
        entities=["Iran", "US", "Strait of Hormuz"],
        affected_asset_ids=[AssetId.BRENT_OIL],
        polarity=EventPolarity.RESOLUTION,
        context_tags=[ConditionCode.TRANSPORT_AFFECTED],
        first_seen_at=seen,
        last_seen_at=seen,
        sources=[],
        fact_conflicts=[],
        extraction_method=ExtractionMethod.LOCAL,
        llm_metadata=None,
    )

    conn = await aio_pika.connect_robust(url)
    async with conn:
        ch = await conn.channel()
        ex = await ch.declare_exchange(
            EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True
        )
        await ex.publish(
            aio_pika.Message(
                body=event.to_amqp_body(),
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            ),
            routing_key=RoutingKey.EVENT_DETECTED.value,
        )
    print(f"published EventDetected event_id={event.event_id} "
          f"type={event.event_type.value} polarity={event.polarity.value} "
          f"tags={[t.value for t in event.context_tags]} asset=BRENT_OIL first_seen={seen.isoformat()}")


if __name__ == "__main__":
    asyncio.run(main())
