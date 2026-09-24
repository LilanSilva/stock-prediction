import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from shared.messaging.intraday_topology import INTRADAY_BINDINGS, ensure_intraday_topology
from shared.schemas.messages import (
    ROUTING_KEY_BY_MESSAGE,
    IntradayBar,
    IntradayObserved,
    IntradayRequested,
)


def test_intraday_round_trip_and_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "shared.schemas.asset_id.is_known_asset", lambda value: value == "SYNTHETIC"
    )
    now = datetime(2026, 9, 21, 13, 30, tzinfo=UTC)
    request = IntradayRequested(
        stream_id=uuid.uuid4(),
        correlation_id=uuid.uuid4(),
        occurred_at=now,
        asset_id="SYNTHETIC",
        registry_version="test",
        calendar_id="XNYS",
        opens_at=now,
        closes_at=now + timedelta(hours=6, minutes=30),
    )
    observed = IntradayObserved(
        correlation_id=request.correlation_id,
        occurred_at=now,
        stream_id=request.stream_id,
        asset_id=request.asset_id,
        registry_version="test",
        revision=1,
        final=True,
        bars=[IntradayBar(start=now, open=100, high=102, low=99, close=101)],
    )
    for message, key in ((request, "intraday.requested"), (observed, "intraday.observed")):
        assert type(message).from_amqp_body(message.to_amqp_body()) == message
        assert ROUTING_KEY_BY_MESSAGE[type(message)] == key


async def test_additive_topology_does_not_redeclare_existing_queues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = AsyncMock()
    connect = AsyncMock(return_value=connection)
    monkeypatch.setattr("shared.messaging.intraday_topology.aio_pika.connect_robust", connect)
    await ensure_intraday_topology("amqp://example.test/")
    channel = connection.channel.return_value
    declared = {call.args[0] for call in channel.declare_queue.call_args_list}
    assert declared == set(INTRADAY_BINDINGS) | {q + ".dlq" for q in INTRADAY_BINDINGS}
    assert channel.declare_exchange.call_count == 0
    connection.close.assert_awaited_once()
