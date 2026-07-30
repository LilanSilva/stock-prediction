"""Unit tests for the credibility pipeline with fake graph + repository (no live infra)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from shared.graph.models import FiringEdge
from shared.schemas.messages import (
    AssetId,
    CloseObservation,
    ContributingEdge,
    Direction,
    EventType,
    Magnitude,
    PredictionScored,
    PriceKind,
)

from credibility.exceptions import InvalidScoredMessageError
from credibility.pipeline import CredibilityPipeline, parse_edge_id
from credibility.updater import WeightUpdate


class FakeGraph:
    """In-memory stand-in for the shared CausalGraphClient."""

    def __init__(self, edges: dict[tuple[EventType, AssetId], tuple[float, float]]) -> None:
        self._edges = edges
        self.writes: list[tuple[EventType, AssetId, float, float]] = []

    async def get_firing_edges(
        self, event_type: EventType, asset_ids: list[AssetId] | None = None
    ) -> list[FiringEdge]:
        out: list[FiringEdge] = []
        for (factor, asset), (alpha, beta) in self._edges.items():
            if factor != event_type:
                continue
            if asset_ids is not None and asset not in asset_ids:
                continue
            out.append(
                FiringEdge(
                    factor_id=factor,
                    asset_id=asset,
                    direction=Direction.UP,
                    weight=0.5,
                    confidence=0.5,
                    alpha=alpha,
                    beta=beta,
                )
            )
        return out

    async def update_edge_weight(
        self, factor_id: EventType, asset_id: AssetId, *, alpha: float, beta: float
    ) -> None:
        self.writes.append((factor_id, asset_id, alpha, beta))
        self._edges[(factor_id, asset_id)] = (alpha, beta)


class FakeRepo:
    """In-memory stand-in for CredibilityRepository."""

    def __init__(
        self,
        *,
        processed: set[uuid.UUID] | None = None,
        sources: dict[str, tuple[float, float]] | None = None,
    ) -> None:
        self._processed = processed or set()
        self._sources = sources or {}
        self.committed: list[tuple[uuid.UUID, list[WeightUpdate]]] = []

    async def already_processed(self, prediction_id: uuid.UUID) -> bool:
        return prediction_id in self._processed

    async def get_source_state(self, source_id: str) -> tuple[float, float] | None:
        return self._sources.get(source_id)

    async def commit_updates(
        self, prediction_id: uuid.UUID, updates: list[WeightUpdate]
    ) -> bool:
        if prediction_id in self._processed:
            return False
        self._processed.add(prediction_id)
        self.committed.append((prediction_id, updates))
        return True


def _close(session: date, close: str) -> CloseObservation:
    return CloseObservation(
        session=session,
        close=Decimal(close),
        fetched_at=datetime.now(UTC),
        source="Yahoo Finance chart",
        provider_symbol="GC=F",
        price_kind=PriceKind.PROVIDER_DAILY_CLOSE,
        is_adjusted=False,
        registry_version="poc6-yahoo-reference-v1",
    )


def _scored(
    *,
    is_correct: bool,
    edges: list[tuple[str, float]],
    sources: list[str],
    prediction_id: uuid.UUID | None = None,
) -> PredictionScored:
    return PredictionScored(
        correlation_id=uuid.uuid4(),
        occurred_at=datetime.now(UTC),
        prediction_id=prediction_id or uuid.uuid4(),
        context_id=uuid.uuid4(),
        asset_id=AssetId.GOLD,
        predicted_direction=Direction.UP,
        actual_direction=Direction.UP if is_correct else Direction.DOWN,
        predicted_magnitude=Magnitude.MEDIUM,
        actual_magnitude=Magnitude.MEDIUM,
        confidence=0.8,
        actual_return=0.02 if is_correct else -0.02,
        is_correct=is_correct,
        score=1.0 if is_correct else 0.0,
        contributing_edges=[
            ContributingEdge(
                edge_id=edge_id,
                direction=Direction.UP,
                current_weight=0.5,
                influence_weight=influence,
                path=edge_id,
            )
            for edge_id, influence in edges
        ],
        source_ids=sources,
        baseline=_close(date(2026, 7, 27), "100.00"),
        settlement=_close(date(2026, 7, 28), "102.00"),
        scored_at=datetime.now(UTC),
    )


def test_parse_edge_id_valid() -> None:
    assert parse_edge_id("MILITARY_CONFLICT->GOLD") == (EventType.MILITARY_CONFLICT, AssetId.GOLD)


def test_parse_edge_id_malformed() -> None:
    with pytest.raises(InvalidScoredMessageError):
        parse_edge_id("no-arrow")


def test_parse_edge_id_unknown_factor() -> None:
    with pytest.raises(InvalidScoredMessageError):
        parse_edge_id("NOT_A_FACTOR->GOLD")


async def test_hit_adds_proportional_credit_to_edge_alpha() -> None:
    graph = FakeGraph(
        {
            (EventType.MILITARY_CONFLICT, AssetId.GOLD): (1.0, 1.0),
            (EventType.INFLATION_CHANGE, AssetId.GOLD): (1.0, 1.0),
        }
    )
    repo = FakeRepo()
    pipeline = CredibilityPipeline(repo, graph, prior_floor=1.0)
    message = _scored(
        is_correct=True,
        edges=[("MILITARY_CONFLICT->GOLD", 0.7), ("INFLATION_CHANGE->GOLD", 0.3)],
        sources=[],
    )
    applied = await pipeline.process(message)
    assert applied is True

    written = {(f, a): (alpha, beta) for f, a, alpha, beta in graph.writes}
    assert written[(EventType.MILITARY_CONFLICT, AssetId.GOLD)] == pytest.approx((1.7, 1.0))
    assert written[(EventType.INFLATION_CHANGE, AssetId.GOLD)] == pytest.approx((1.3, 1.0))


async def test_miss_adds_proportional_credit_to_edge_beta() -> None:
    graph = FakeGraph({(EventType.SANCTIONS, AssetId.BRENT_OIL): (2.0, 2.0)})
    repo = FakeRepo()
    pipeline = CredibilityPipeline(repo, graph, prior_floor=1.0)
    message = _scored(is_correct=False, edges=[("SANCTIONS->BRENT_OIL", 0.5)], sources=[])
    await pipeline.process(message)

    factor, asset, alpha, beta = graph.writes[0]
    assert (alpha, beta) == pytest.approx((2.0, 3.0))  # sole edge -> full credit 1.0 to beta


async def test_missing_edge_is_skipped_not_fatal() -> None:
    graph = FakeGraph({(EventType.MILITARY_CONFLICT, AssetId.GOLD): (1.0, 1.0)})
    repo = FakeRepo()
    pipeline = CredibilityPipeline(repo, graph, prior_floor=1.0)
    message = _scored(
        is_correct=True,
        edges=[("MILITARY_CONFLICT->GOLD", 0.5), ("RECESSION_SIGNAL->GOLD", 0.5)],
        sources=[],
    )
    applied = await pipeline.process(message)
    assert applied is True
    # Only the present edge is written; the missing one is skipped.
    assert len(graph.writes) == 1
    _, prediction_updates = repo.committed[0]
    edge_ids = {u.entity_id for u in prediction_updates if u.entity_type == "edge"}
    assert edge_ids == {"MILITARY_CONFLICT->GOLD"}


async def test_sources_get_equal_credit_and_new_source_starts_at_prior() -> None:
    graph = FakeGraph({})
    repo = FakeRepo(sources={"di.se": (3.0, 1.0)})  # returning source
    pipeline = CredibilityPipeline(repo, graph, prior_floor=1.0)
    message = _scored(is_correct=True, edges=[], sources=["di.se", "svd.se"])
    await pipeline.process(message)

    _, updates = repo.committed[0]
    by_id = {u.entity_id: u for u in updates}
    # di.se was at (3,1) -> +0.5 alpha
    assert (by_id["di.se"].alpha_after, by_id["di.se"].beta_after) == pytest.approx((3.5, 1.0))
    # svd.se is new -> starts from prior (1,1) then +0.5 alpha
    assert (by_id["svd.se"].alpha_after, by_id["svd.se"].beta_after) == pytest.approx((1.5, 1.0))


async def test_duplicate_prediction_is_skipped() -> None:
    pid = uuid.uuid4()
    graph = FakeGraph({(EventType.SANCTIONS, AssetId.GOLD): (1.0, 1.0)})
    repo = FakeRepo(processed={pid})
    pipeline = CredibilityPipeline(repo, graph, prior_floor=1.0)
    message = _scored(
        is_correct=True, edges=[("SANCTIONS->GOLD", 0.5)], sources=["di.se"], prediction_id=pid
    )
    applied = await pipeline.process(message)
    assert applied is False
    assert graph.writes == []  # no edge writes on a duplicate
    assert repo.committed == []


async def test_empty_sources_still_processes() -> None:
    graph = FakeGraph({(EventType.SANCTIONS, AssetId.GOLD): (1.0, 1.0)})
    repo = FakeRepo()
    pipeline = CredibilityPipeline(repo, graph, prior_floor=1.0)
    message = _scored(is_correct=True, edges=[("SANCTIONS->GOLD", 1.0)], sources=[])
    applied = await pipeline.process(message)
    assert applied is True
    _, updates = repo.committed[0]
    assert all(u.entity_type == "edge" for u in updates)
