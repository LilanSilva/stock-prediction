"""Additive queues for sampled evidence and independent shadow prediction registration."""

import aio_pika

SNAPSHOT_BINDINGS = {
    "verification.price-samples": "price.sample.observed",
    "verification.sample-predictions": "prediction.made",
}


async def ensure_snapshot_topology(url: str) -> None:
    connection = await aio_pika.connect_robust(url)
    try:
        channel = await connection.channel()
        events = await channel.get_exchange("feed.events", ensure=True)
        dlx = await channel.get_exchange("feed.dlx", ensure=True)
        for name, key in SNAPSHOT_BINDINGS.items():
            dead = await channel.declare_queue(
                name + ".dlq", durable=True, arguments={"x-queue-type": "classic"}
            )
            # A unique dead-letter key prevents prediction failures reaching other owners' DLQs.
            dead_key = name + ".failed"
            await dead.bind(dlx, routing_key=dead_key)
            queue = await channel.declare_queue(
                name,
                durable=True,
                arguments={
                    "x-queue-type": "classic",
                    "x-dead-letter-exchange": "feed.dlx",
                    "x-dead-letter-routing-key": dead_key,
                },
            )
            await queue.bind(events, routing_key=key)
    finally:
        await connection.close()
