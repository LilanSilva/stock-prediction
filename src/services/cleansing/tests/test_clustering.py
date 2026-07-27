from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from shared.schemas.messages import EventType

from cleansing.clustering import (
    ClusterCandidate,
    can_accept_article,
    decide_assignment,
    is_ready_to_close,
    lifetime_deadline,
    quiet_deadline,
    update_centroid,
)
from cleansing.models import ClusterState


def _candidate(similarity: float, event_type: EventType) -> ClusterCandidate:
    return ClusterCandidate(cluster_id=uuid.uuid4(), event_type=event_type, similarity=similarity)


def test_creates_new_cluster_when_no_candidates() -> None:
    decision = decide_assignment(EventType.SANCTIONS, [], similarity_threshold=0.80)
    assert decision.cluster_id is None


def test_gate1_below_threshold_creates_new() -> None:
    candidates = [_candidate(0.70, EventType.SANCTIONS)]
    decision = decide_assignment(EventType.SANCTIONS, candidates, similarity_threshold=0.80)
    assert decision.cluster_id is None


def test_gate2_incompatible_creates_new() -> None:
    # High similarity but a different canonical type must NOT merge (acceptance criterion 3).
    candidates = [_candidate(0.99, EventType.STRAIT_CLOSURE)]
    decision = decide_assignment(EventType.MILITARY_CONFLICT, candidates, similarity_threshold=0.80)
    assert decision.cluster_id is None


def test_both_gates_pass_joins_best() -> None:
    weak = _candidate(0.82, EventType.RATE_DECISION)
    strong = _candidate(0.95, EventType.RATE_DECISION)
    decision = decide_assignment(
        EventType.RATE_DECISION, [weak, strong], similarity_threshold=0.80
    )
    assert decision.cluster_id == strong.cluster_id


def test_other_never_joins() -> None:
    candidates = [_candidate(1.0, EventType.OTHER)]
    decision = decide_assignment(EventType.OTHER, candidates, similarity_threshold=0.80)
    assert decision.cluster_id is None


def test_ready_to_close_on_quiet_or_lifetime() -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    assert is_ready_to_close(
        now, quiet_at=now - timedelta(minutes=1), lifetime_at=now + timedelta(hours=1)
    )
    assert is_ready_to_close(
        now, quiet_at=now + timedelta(minutes=1), lifetime_at=now - timedelta(seconds=1)
    )
    assert not is_ready_to_close(
        now, quiet_at=now + timedelta(minutes=1), lifetime_at=now + timedelta(hours=1)
    )


def test_deadlines() -> None:
    first = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    assert quiet_deadline(first, 30) == first + timedelta(minutes=30)
    assert lifetime_deadline(first, 24) == first + timedelta(hours=24)


def test_update_centroid_running_mean() -> None:
    assert update_centroid([], 0, [1.0, 0.0]) == [1.0, 0.0]
    # One existing vector [2,0], add [0,0] -> mean [1,0].
    assert update_centroid([2.0, 0.0], 1, [0.0, 0.0]) == [1.0, 0.0]


def test_only_open_quiet_accept() -> None:
    assert can_accept_article(ClusterState.OPEN)
    assert can_accept_article(ClusterState.QUIET)
    assert not can_accept_article(ClusterState.READY)
    assert not can_accept_article(ClusterState.MERGED)
