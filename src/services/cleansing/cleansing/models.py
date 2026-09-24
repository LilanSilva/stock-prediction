"""Internal value objects for the Cleansing pipeline.

These are deliberately separate from the canonical wire models in shared.schemas.messages: they
carry processing-only state (fingerprints, cluster timers, extraction provenance) that never leaves
the service.
"""

from __future__ import annotations

import builtins
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from shared.schemas.messages import AssetId, ConditionCode, EventPolarity, EventType


class ClusterState(StrEnum):
    """Cluster lifecycle (functional document sec 4). Forward-only, atomic transitions."""

    OPEN = "OPEN"
    QUIET = "QUIET"
    READY = "READY"
    MERGING = "MERGING"
    MERGED = "MERGED"
    ERROR_RETRYABLE = "ERROR_RETRYABLE"
    ERROR_TERMINAL = "ERROR_TERMINAL"


@dataclass(frozen=True)
class ArticleFacts:
    """Normalized fields extracted from an ArticleIngested message for processing."""

    article_id: uuid.UUID
    source_id: str
    canonical_url: str
    title: str
    body: str
    language: str
    country: str
    published_at: datetime
    correlation_id: uuid.UUID


@dataclass(frozen=True)
class ExtractedAction:
    """Local NLP output mapped to the canonical taxonomy.

    `original_lemma` retains the source verb even when the mapping falls through to OTHER, so the
    taxonomy can be reviewed and extended later without losing audit information.
    """

    actor: str | None
    action_lemma: str | None
    object: str | None
    original_lemma: str | None
    event_type: EventType
    affected_asset_ids: tuple[AssetId, ...] = ()
    polarity: EventPolarity = EventPolarity.OCCURRENCE
    context_tags: tuple[ConditionCode, ...] = ()
    classification_audit: dict[str, builtins.object] = field(default_factory=dict)


@dataclass
class ClusterRecord:
    """In-flight cluster state loaded from / persisted to PostgreSQL."""

    cluster_id: uuid.UUID
    event_type: EventType
    state: ClusterState
    article_count: int
    first_seen_at: datetime
    last_seen_at: datetime
    quiet_deadline: datetime
    lifetime_deadline: datetime
    centroid: list[float] = field(default_factory=list)
