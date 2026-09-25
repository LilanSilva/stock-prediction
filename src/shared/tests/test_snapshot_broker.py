"""Optional broker contract test; use only a disposable SNAPSHOT_TEST_RABBITMQ_URL."""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import aio_pika
import pytest

from shared.messaging.client import RabbitMQClient
from shared.messaging.snapshot_topology import ensure_snapshot_topology
from shared.schemas.messages import PriceSampleObserved


@pytest.mark.integration
async def test_snapshot_additive_migration_and_real_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = os.environ.get("SNAPSHOT_TEST_RABBITMQ_URL")
    if not url:
        pytest.skip("requires disposable SNAPSHOT_TEST_RABBITMQ_URL")
    for attempt in range(40):
        try:
            connection = await aio_pika.connect(url, timeout=2)
            break
        except (OSError, ConnectionError):
            if attempt == 39:
                raise
            await asyncio.sleep(1)
    async with connection:
        channel = await connection.channel()
        await channel.declare_exchange("feed.events", aio_pika.ExchangeType.TOPIC, durable=True)
        await channel.declare_exchange("feed.dlx", aio_pika.ExchangeType.TOPIC, durable=True)
        # A legacy queue proves the migration leaves preexisting work intact.
        legacy = await channel.declare_queue("test.legacy", durable=True)
        await channel.default_exchange.publish(aio_pika.Message(b"keep"), routing_key=legacy.name)
        await ensure_snapshot_topology(url)
        await ensure_snapshot_topology(url)
        original = await legacy.get(timeout=5)
        assert original is not None and original.body == b"keep"
        await original.ack()
        monkeypatch.setattr(
            "shared.schemas.asset_id.is_known_asset", lambda value: value == "BROKER_TEST"
        )
        now = datetime.now(UTC).replace(second=0, microsecond=0)
        request = PriceSampleObserved(
            correlation_id=uuid.uuid4(),
            occurred_at=now,
            sample_id=uuid.uuid4(),
            mapping_version="test",
            session=now.date(),
            scheduled_at=now,
            observed_at=now,
            price="100.25",
            currency="SEK",
            quote_unit="SEK",
            kind="REGULAR",
            quality="FRESHNESS_UNKNOWN",
            market_state="REGULAR_OPEN",
            asset_id="BROKER_TEST",
            registry_version="test",
            opens_at=now,
            closes_at=now + timedelta(hours=1),
        )
        async with RabbitMQClient(url) as client:
            await client.publish(request)
        queue = await channel.get_queue("verification.price-samples")
        received = await queue.get(timeout=5)
        assert received is not None
        assert PriceSampleObserved.model_validate_json(received.body) == request
        await received.nack(requeue=False)
        dlq = await channel.get_queue("verification.price-samples.dlq")
        async with dlq.iterator(timeout=5) as messages:
            rejected = await anext(messages)
            assert rejected.body == received.body
            await rejected.ack()
