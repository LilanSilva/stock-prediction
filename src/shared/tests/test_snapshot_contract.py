"""Point samples have their own immutable contract and independent queue/DLQ."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from shared.messaging.snapshot_topology import SNAPSHOT_BINDINGS
from shared.schemas.messages import ROUTING_KEY_BY_MESSAGE, PriceSampleObserved, RoutingKey


def test_sample_roundtrip_currency_and_timestamp_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("shared.schemas.asset_id.is_known_asset", lambda x: x == "TEST_STOCK")
    now = datetime.now(UTC)
    data = dict(
        sample_id=uuid.uuid4(),
        correlation_id=uuid.uuid4(),
        occurred_at=now,
        asset_id="TEST_STOCK",
        mapping_version="v1",
        registry_version="r1",
        session=now.date(),
        opens_at=now,
        closes_at=now + timedelta(hours=1),
        scheduled_at=now,
        observed_at=now,
        price="123.45",
        currency="DKK",
        quote_unit="DKK",
        kind="REGULAR",
        market_state="REGULAR_OPEN",
        quality="FRESHNESS_UNKNOWN",
    )
    sample = PriceSampleObserved.model_validate(data)
    assert PriceSampleObserved.from_amqp_body(sample.to_amqp_body()) == sample
    assert ROUTING_KEY_BY_MESSAGE[type(sample)] == RoutingKey.PRICE_SAMPLE_OBSERVED
    assert json.loads(sample.model_dump_json())["price"] == "123.45"
    invalid: list[dict[str, object]] = [
        {"price": "NaN"},
        {"quality": "FRESH"},
        {"currency": "kr"},
        {"provider_quote_at": now + timedelta(seconds=1)},
        {"observed_at": now - timedelta(seconds=1)},
    ]
    for changes in invalid:
        with pytest.raises(ValueError):
            PriceSampleObserved.model_validate({**data, **changes})


def test_snapshot_topology_has_dedicated_dead_letter_routes() -> None:
    definitions = json.loads(
        (Path(__file__).parents[3] / "infra/rabbitmq/definitions.json").read_text()
    )
    queues = {q["name"]: q for q in definitions["queues"]}
    bindings = {(b["source"], b["destination"], b["routing_key"]) for b in definitions["bindings"]}
    for name, key in SNAPSHOT_BINDINGS.items():
        assert queues[name]["durable"]
        assert queues[name]["arguments"]["x-dead-letter-routing-key"] == name + ".failed"
        assert ("feed.events", name, key) in bindings
        assert ("feed.dlx", name + ".dlq", name + ".failed") in bindings
