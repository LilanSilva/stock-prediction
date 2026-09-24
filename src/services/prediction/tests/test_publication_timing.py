import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from shared.schemas.messages import PredictionMade

from prediction.outbox import PredictionOutboxPublisher


async def test_publication_attempt_does_not_change_decision_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "shared.schemas.asset_id.is_known_asset", lambda value: value == "TIMING_TEST"
    )
    decided = datetime(2026, 9, 21, 15, tzinfo=UTC)
    message = PredictionMade(
        correlation_id=uuid.uuid4(),
        occurred_at=decided,
        prediction_id=uuid.uuid4(),
        context_id=uuid.uuid4(),
        context_version=1,
        event_ids=[],
        asset_id="TIMING_TEST",
        direction="UP",
        magnitude="SMALL",
        confidence=0.7,
        horizon="ONE_TRADING_DAY",
        rationale="test",
        decision_at=decided,
        decision_method="GRAPH_ONLY",
    )
    # Older payloads remain valid and publication timing is explicitly unknown before dispatch.
    assert message.publication_attempt_at is None
    pool, publisher = AsyncMock(), AsyncMock()
    pool.fetch.return_value = [
        {"message_id": message.message_id, "payload": message.model_dump_json()}
    ]
    await PredictionOutboxPublisher(pool, publisher).publish_pending()
    delivered = publisher.publish.call_args.args[0]
    assert delivered.decision_at == decided
    assert delivered.publication_attempt_at is not None
    assert delivered.publication_attempt_at >= decided
    assert message.publication_attempt_at is None
