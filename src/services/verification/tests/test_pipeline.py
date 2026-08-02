from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from shared.schemas.messages import (
    AssetId,
    CloseObservation,
    ContributingEdge,
    DecisionMethod,
    Direction,
    Horizon,
    Magnitude,
    PredictionMade,
    PredictionScored,
    PriceKind,
    PriceObserved,
    PriceRequested,
)

from verification.config import VerificationSettings
from verification.exceptions import PriceValidationError
from verification.models import EvaluationRecord, EvaluationStatus
from verification.pipeline import VerificationPipeline

_REGISTRY = "biquote-reference-v1"
_DECISION_AT = datetime(2026, 7, 27, 22, 46, tzinfo=UTC)  # Monday after the 17:00 NY close


def _prediction(
    asset: AssetId = AssetId.GOLD,
    direction: Direction = Direction.UP,
    supersedes: uuid.UUID | None = None,
) -> PredictionMade:
    return PredictionMade(
        correlation_id=uuid.uuid4(),
        occurred_at=_DECISION_AT,
        prediction_id=uuid.uuid4(),
        context_id=uuid.uuid4(),
        context_version=1,
        event_ids=[uuid.uuid4()],
        asset_id=asset,
        direction=direction,
        magnitude=Magnitude.MEDIUM,
        confidence=1.0,
        horizon=Horizon.ONE_TRADING_DAY,
        rationale="test",
        contributing_edges=[
            ContributingEdge(
                edge_id="SANCTIONS->GOLD",
                direction=Direction.UP,
                current_weight=0.5,
                influence_weight=0.55,
                path="SANCTIONS->GOLD",
            )
        ],
        decision_at=_DECISION_AT,
        supersedes_prediction_id=supersedes,
        decision_method=DecisionMethod.GRAPH_ONLY,
    )


def _evaluation(
    request_id: uuid.UUID,
    *,
    direction: Direction = Direction.UP,
    status: EvaluationStatus = EvaluationStatus.PENDING,
) -> EvaluationRecord:
    return EvaluationRecord(
        prediction_id=uuid.uuid4(),
        context_id=uuid.uuid4(),
        asset_id=AssetId.GOLD,
        predicted_direction=direction,
        predicted_magnitude=Magnitude.MEDIUM,
        confidence=1.0,
        decision_at=_DECISION_AT,
        baseline_session=date(2026, 7, 27),
        settlement_session=date(2026, 7, 28),
        market_calendar="America/New_York",
        registry_version=_REGISTRY,
        request_id=request_id,
        correlation_id=uuid.uuid4(),
        contributing_edges=[],
        source_ids=[],
        status=status,
    )


def _close(session: date, close: str) -> CloseObservation:
    return CloseObservation(
        session=session,
        close=Decimal(close),
        fetched_at=datetime.now(UTC),
        source="biquote.io",
        provider_symbol="XAUUSD",
        price_kind=PriceKind.PROVIDER_DAILY_CLOSE,
        is_adjusted=False,
        registry_version=_REGISTRY,
    )


def _observed(
    request_id: uuid.UUID,
    baseline_close: str,
    settlement_close: str,
    *,
    baseline_session: date = date(2026, 7, 27),
    settlement_session: date = date(2026, 7, 28),
    registry_version: str = _REGISTRY,
) -> PriceObserved:
    baseline = _close(baseline_session, baseline_close)
    settlement = _close(settlement_session, settlement_close)
    baseline = baseline.model_copy(update={"registry_version": registry_version})
    settlement = settlement.model_copy(update={"registry_version": registry_version})
    return PriceObserved(
        correlation_id=uuid.uuid4(),
        occurred_at=datetime.now(UTC),
        request_id=request_id,
        prediction_id=uuid.uuid4(),
        asset_id=AssetId.GOLD,
        baseline=baseline,
        settlement=settlement,
    )


class _FakeRepo:
    def __init__(
        self,
        *,
        create_result: bool = True,
        store_score_result: bool = True,
        evaluation: EvaluationRecord | None = None,
    ) -> None:
        self.created: list[tuple[EvaluationRecord, PriceRequested]] = []
        self.observations: list[PriceObserved] = []
        self.scored: list[PredictionScored] = []
        self._create_result = create_result
        self._store_score_result = store_score_result
        self._evaluation = evaluation
        self.withdrawn: list[uuid.UUID] = []

    async def create_evaluation_with_outbox(
        self, evaluation: EvaluationRecord, request: PriceRequested
    ) -> bool:
        self.created.append((evaluation, request))
        return self._create_result

    async def withdraw_evaluation(self, prediction_id: uuid.UUID) -> bool:
        self.withdrawn.append(prediction_id)
        return True

    async def store_price_observation(self, message: PriceObserved) -> bool:
        self.observations.append(message)
        return True

    async def load_evaluation_by_request_id(
        self, request_id: uuid.UUID
    ) -> EvaluationRecord | None:
        return self._evaluation

    async def store_score_with_outbox(self, scored: PredictionScored) -> bool:
        self.scored.append(scored)
        return self._store_score_result


def _pipeline(repo: _FakeRepo) -> VerificationPipeline:
    return VerificationPipeline(repo, VerificationSettings())


async def test_process_prediction_resolves_sessions_and_requests_price() -> None:
    repo = _FakeRepo()
    prediction = _prediction()
    await _pipeline(repo).process_prediction(prediction)

    assert len(repo.created) == 1
    evaluation, request = repo.created[0]
    assert request.baseline_session == date(2026, 7, 27)
    assert request.settlement_session == date(2026, 7, 28)
    assert request.asset_id == AssetId.GOLD
    assert request.market_calendar == "America/New_York"
    assert evaluation.registry_version == _REGISTRY
    assert request.prediction_id == prediction.prediction_id


async def test_request_id_is_deterministic() -> None:
    prediction = _prediction()
    repo_a, repo_b = _FakeRepo(), _FakeRepo()
    await _pipeline(repo_a).process_prediction(prediction)
    await _pipeline(repo_b).process_prediction(prediction)
    assert repo_a.created[0][1].request_id == repo_b.created[0][1].request_id


async def test_process_prediction_withdraws_superseded() -> None:
    repo = _FakeRepo()
    superseded = uuid.uuid4()
    await _pipeline(repo).process_prediction(_prediction(supersedes=superseded))
    # A weekend-collapse supersede must withdraw the prior stance's evaluation.
    assert repo.withdrawn == [superseded]


async def test_process_prediction_without_supersede_withdraws_nothing() -> None:
    repo = _FakeRepo()
    await _pipeline(repo).process_prediction(_prediction())
    assert repo.withdrawn == []


async def test_process_price_skips_withdrawn_evaluation() -> None:
    request_id = uuid.uuid4()
    repo = _FakeRepo(
        evaluation=_evaluation(request_id, status=EvaluationStatus.WITHDRAWN)
    )
    await _pipeline(repo).process_price(_observed(request_id, "100.00", "103.00"))
    # A withdrawn (superseded) evaluation is never scored.
    assert repo.scored == []
    assert repo.observations == []


async def test_process_price_scores_and_publishes() -> None:
    request_id = uuid.uuid4()
    repo = _FakeRepo(evaluation=_evaluation(request_id, direction=Direction.UP))
    await _pipeline(repo).process_price(_observed(request_id, "100.00", "103.00"))

    assert len(repo.scored) == 1
    scored = repo.scored[0]
    assert scored.actual_direction == Direction.UP  # +3%
    assert scored.actual_magnitude == Magnitude.LARGE
    assert scored.is_correct is True
    assert scored.score == 1.0
    assert len(repo.observations) == 1


async def test_process_price_without_evaluation_is_rejected() -> None:
    repo = _FakeRepo(evaluation=None)
    with pytest.raises(PriceValidationError, match="no evaluation"):
        await _pipeline(repo).process_price(_observed(uuid.uuid4(), "100", "103"))


async def test_process_price_session_mismatch_is_rejected() -> None:
    request_id = uuid.uuid4()
    repo = _FakeRepo(evaluation=_evaluation(request_id))
    bad = _observed(request_id, "100", "103", settlement_session=date(2026, 7, 29))
    with pytest.raises(PriceValidationError, match="settlement session mismatch"):
        await _pipeline(repo).process_price(bad)


async def test_process_price_registry_mismatch_is_rejected() -> None:
    request_id = uuid.uuid4()
    repo = _FakeRepo(evaluation=_evaluation(request_id))
    bad = _observed(request_id, "100", "103", registry_version="other-policy-v9")
    with pytest.raises(PriceValidationError, match="registry version mismatch"):
        await _pipeline(repo).process_price(bad)


async def test_conditioned_edge_id_passes_through_unchanged() -> None:
    # A 3-part "FACTOR|CONDITION->ASSET" edge_id is opaque: it must survive PredictionMade ->
    # evaluation -> PredictionScored verbatim (no split on "->" or "|", no truncation).
    conditioned = "MILITARY_CONFLICT|TRANSPORT_AFFECTED->BRENT_OIL"
    edge = ContributingEdge(
        edge_id=conditioned,
        direction=Direction.UP,
        current_weight=0.65,
        influence_weight=0.65,
        path=conditioned,
    )
    prediction = _prediction(asset=AssetId.BRENT_OIL).model_copy(
        update={"contributing_edges": [edge]}
    )

    create_repo = _FakeRepo()
    await _pipeline(create_repo).process_prediction(prediction)
    evaluation, _request = create_repo.created[0]
    assert [e.edge_id for e in evaluation.contributing_edges] == [conditioned]

    observed = _observed(evaluation.request_id, "100.00", "103.00").model_copy(
        update={"asset_id": AssetId.BRENT_OIL}
    )
    score_repo = _FakeRepo(evaluation=evaluation)
    await _pipeline(score_repo).process_price(observed)

    assert [e.edge_id for e in score_repo.scored[0].contributing_edges] == [conditioned]
