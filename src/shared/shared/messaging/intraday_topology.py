"""Additive topology migration for existing brokers; leaves all existing queues intact."""

from __future__ import annotations

import aio_pika

INTRADAY_BINDINGS = {
    "market-data.intraday-requests": "intraday.requested",
    "verification.intraday-prices": "intraday.observed",
}


async def ensure_intraday_topology(url: str) -> None:
    """Idempotently declare only intraday queues before enabling producers or consumers."""
    connection = await aio_pika.connect_robust(url)
    try:
        channel = await connection.channel()
        events = await channel.get_exchange("feed.events", ensure=True)
        dlx = await channel.get_exchange("feed.dlx", ensure=True)
        for name, key in INTRADAY_BINDINGS.items():
            dead = await channel.declare_queue(
                name + ".dlq", durable=True, arguments={"x-queue-type": "classic"}
            )
            await dead.bind(dlx, routing_key=key)
            queue = await channel.declare_queue(
                name,
                durable=True,
                arguments={
                    "x-queue-type": "classic",
                    "x-dead-letter-exchange": "feed.dlx",
                    "x-dead-letter-routing-key": key,
                },
            )
            await queue.bind(events, routing_key=key)
    finally:
        await connection.close()
