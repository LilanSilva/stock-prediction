import uuid
from collections.abc import Callable
from datetime import datetime

import pytest
from pydantic import ValidationError

from shared.schemas.messages import (
    ROUTING_KEY_BY_MESSAGE,
    ArticleIngested,
    AssetId,
    DecisionMethod,
    FeedMessage,
    PredictionMade,
    RoutingKey,
)
from tests.factories import ALL_FACTORIES, make_article, make_prediction


@pytest.mark.parametrize("factory", ALL_FACTORIES)
def test_round_trip_amqp_body(factory: Callable[[], FeedMessage]) -> None:
    original: FeedMessage = factory()
    restored = type(original).from_amqp_body(original.to_amqp_body())
    assert restored == original


@pytest.mark.parametrize("factory", ALL_FACTORIES)
def test_every_message_has_full_envelope(factory: Callable[[], FeedMessage]) -> None:
    msg: FeedMessage = factory()
    assert isinstance(msg.message_id, uuid.UUID)
    assert isinstance(msg.correlation_id, uuid.UUID)
    assert msg.schema_version == "1.0"
    assert msg.occurred_at.tzinfo is not None


def test_naive_datetime_coerced_to_utc() -> None:
    msg = ArticleIngested(
        correlation_id=uuid.uuid4(),
        occurred_at=datetime(2026, 7, 13, 12, 0),  # naive
        article_id=uuid.uuid4(),
        source_id="di",
        canonical_url="https://example.se/a",
        title="t",
        body="b",
        published_at=datetime(2026, 7, 13, 11, 0),  # naive
        language="sv",
        country="SE",
        content_hash="h",
    )
    assert msg.occurred_at.tzinfo is not None
    assert msg.published_at.tzinfo is not None


def test_frozen_messages_reject_mutation() -> None:
    msg = make_article()
    with pytest.raises(ValidationError):
        msg.title = "new"  # type: ignore[misc]


def test_invalid_correlation_id_type_rejected() -> None:
    with pytest.raises(ValidationError):
        ArticleIngested(
            correlation_id="not-a-uuid",
            occurred_at=datetime(2026, 7, 13, 12, 0),
            article_id=uuid.uuid4(),
            source_id="di",
            canonical_url="u",
            title="t",
            body="b",
            published_at=datetime(2026, 7, 13, 11, 0),
            language="sv",
            country="SE",
            content_hash="h",
        )


def test_confidence_out_of_range_rejected() -> None:
    base = make_prediction().model_dump()
    base["confidence"] = 1.5
    with pytest.raises(ValidationError):
        PredictionMade.model_validate(base)


def test_m1_prediction_is_graph_only() -> None:
    assert make_prediction().decision_method is DecisionMethod.GRAPH_ONLY


def test_only_canonical_assets_are_accepted() -> None:
    base = make_prediction().model_dump()
    base["asset_id"] = "GC=F"
    with pytest.raises(ValidationError):
        PredictionMade.model_validate(base)
    assert make_prediction().asset_id is AssetId.GOLD


def test_routing_keys_match_contract() -> None:
    assert ROUTING_KEY_BY_MESSAGE[ArticleIngested] is RoutingKey.ARTICLE_INGESTED
    assert ROUTING_KEY_BY_MESSAGE[PredictionMade] is RoutingKey.PREDICTION_MADE
    # Every message type has a registered routing key.
    assert len(ROUTING_KEY_BY_MESSAGE) == 6
