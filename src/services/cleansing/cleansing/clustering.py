"""Conservative dual-gate clustering decisions and lifecycle timing (pure logic).

This module holds the *decisions* only; all PostgreSQL/pgvector state lives in `repository.py`. Two
gates control whether an article joins an existing cluster (functional document sec 3):

  - Gate 1: cosine similarity to a candidate cluster centroid meets the threshold (default 0.80).
  - Gate 2: the article's canonical event type is compatible with the cluster's type.

Both gates must pass. When no candidate passes, a new cluster is opened. This is deliberately
conservative: article count never closes a cluster, and distinct causal events (e.g. a military
conflict vs. a strait closure) stay separate even at high textual similarity (acceptance
criterion 3).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from shared.schemas.messages import EventType

from cleansing.models import ClusterState
from cleansing.taxonomy import gate2_compatible


@dataclass(frozen=True)
class ClusterCandidate:
    cluster_id: uuid.UUID
    event_type: EventType
    similarity: float


@dataclass(frozen=True)
class AssignmentDecision:
    """Either join an existing cluster (cluster_id set) or create a new one (cluster_id None)."""

    cluster_id: uuid.UUID | None
    reason: str


def decide_assignment(
    article_event_type: EventType,
    candidates: list[ClusterCandidate],
    *,
    similarity_threshold: float,
) -> AssignmentDecision:
    """Pick the best gate-passing candidate, or decide to create a new cluster.

    Candidates that fail either gate are ignored. Among passing candidates the highest similarity
    wins. A new cluster is created when nothing passes (including every OTHER-typed article, which
    is never Gate-2 compatible).
    """
    best: ClusterCandidate | None = None
    for candidate in candidates:
        if candidate.similarity < similarity_threshold:
            continue
        if not gate2_compatible(article_event_type, candidate.event_type):
            continue
        if best is None or candidate.similarity > best.similarity:
            best = candidate

    if best is None:
        return AssignmentDecision(cluster_id=None, reason="no_gate_passing_candidate")
    return AssignmentDecision(cluster_id=best.cluster_id, reason="gate1_gate2_passed")


def quiet_deadline(last_seen_at: datetime, quiet_period_minutes: int) -> datetime:
    """When the cluster becomes ready if no further matching article arrives."""
    return last_seen_at + timedelta(minutes=quiet_period_minutes)


def lifetime_deadline(first_seen_at: datetime, max_lifetime_hours: int) -> datetime:
    """Hard watermark: the cluster closes at this time even if activity continues."""
    return first_seen_at + timedelta(hours=max_lifetime_hours)


def is_ready_to_close(
    now: datetime,
    *,
    quiet_at: datetime,
    lifetime_at: datetime,
) -> bool:
    """A cluster is ready when the quiet period elapsed or the max-lifetime watermark passed."""
    return now >= quiet_at or now >= lifetime_at


def update_centroid(
    centroid: list[float], article_count: int, new_vector: list[float]
) -> list[float]:
    """Incremental running-mean centroid update for a newly added article.

    `article_count` is the count BEFORE adding the new vector. An empty centroid (new cluster)
    adopts the vector directly.
    """
    if not centroid:
        return list(new_vector)
    if len(centroid) != len(new_vector):
        raise ValueError("centroid and new vector must have equal length")
    total = article_count
    return [
        (existing * total + incoming) / (total + 1)
        for existing, incoming in zip(centroid, new_vector, strict=True)
    ]


def can_accept_article(state: ClusterState) -> bool:
    """Only OPEN/QUIET clusters accept new articles; READY clusters are immutable."""
    return state in {ClusterState.OPEN, ClusterState.QUIET}
