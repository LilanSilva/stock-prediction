"""Immutable research evidence capture, isolated from official prediction delivery.

This first stage records opportunities, including abstentions. It deliberately cannot publish
messages, score outcomes, train a model, or change the official predictor.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

import asyncpg
import structlog
from shared.graph import FiringEdge
from shared.schemas.messages import EventDetected

from prediction.config import PredictionSettings
from prediction.models import ContextEvent, ContextRecord, Decision

logger = structlog.get_logger(__name__)
CAPTURE_VERSION = "opportunity-evidence-v1"
NAMESPACE = uuid.UUID("cd6eeb1b-30a8-4813-85be-699477021c59")


def canonical_json(value: object) -> str:
    """Reject nonfinite numbers and produce stable, hashable JSON."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def content_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EventEvidence:
    event_id: uuid.UUID
    content_hash: str
    payload: str
    received_at: datetime


@dataclass(frozen=True)
class CapturedOpportunity:
    opportunity_id: uuid.UUID
    context_id: uuid.UUID
    context_version: int
    asset_id: str
    cutoff: datetime
    snapshot: str
    snapshot_hash: str
    quality: Literal["VALID", "INVALID_INPUT"]
    result_status: Literal["PREDICTED", "ABSTAINED", "FAILED"]
    result: str


class EvidenceStore(Protocol):
    async def record_event(self, event: EventDetected) -> None: ...

    async def load_events(
        self, event_ids: list[uuid.UUID], cutoff: datetime
    ) -> list[EventEvidence]: ...

    async def save(self, opportunity: CapturedOpportunity) -> bool: ...


class ResearchRepository:
    """Append-only evidence; the first capture of a context version wins on redelivery."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def record_event(self, event: EventDetected) -> None:
        # Envelope identifiers describe delivery, not a new semantic event revision.
        payload = canonical_json(event.model_dump(mode="json", exclude={
            "message_id", "correlation_id", "causation_id", "occurred_at",
        }))
        await self._pool.execute(
            """INSERT INTO prediction.research_event_versions
                   (event_id, content_hash, payload)
               VALUES ($1, $2, $3) ON CONFLICT (event_id, content_hash) DO NOTHING""",
            event.event_id, content_hash(payload), payload,
        )

    async def load_events(
        self, event_ids: list[uuid.UUID], cutoff: datetime
    ) -> list[EventEvidence]:
        rows = await self._pool.fetch(
            """SELECT event_id, content_hash, payload, first_received_at
               FROM prediction.research_event_versions
               WHERE event_id = ANY($1::uuid[]) AND first_received_at <= $2
               ORDER BY event_id, content_hash""", event_ids, cutoff,
        )
        return [EventEvidence(
            row["event_id"], row["content_hash"], row["payload"], row["first_received_at"],
        ) for row in rows]

    async def save(self, opportunity: CapturedOpportunity) -> bool:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                inserted = await conn.fetchval(
                    """INSERT INTO prediction.research_opportunities
                       (opportunity_id, context_id, context_version, asset_id, capture_version,
                        feature_cutoff, snapshot, snapshot_hash, quality)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                       ON CONFLICT (context_id, capture_version) DO NOTHING
                       RETURNING opportunity_id""",
                    opportunity.opportunity_id, opportunity.context_id,
                    opportunity.context_version, opportunity.asset_id, CAPTURE_VERSION,
                    opportunity.cutoff, opportunity.snapshot, opportunity.snapshot_hash,
                    opportunity.quality,
                )
                if inserted is not None:
                    await conn.execute(
                        """INSERT INTO prediction.research_predictions
                           (opportunity_id, predictor_id, result_status, payload)
                           VALUES ($1, 'KG_LIVE_CAPTURE', $2, $3)""",
                        opportunity.opportunity_id, opportunity.result_status, opportunity.result,
                    )
            # This timestamp is recorded AFTER the pair commits. An INSERT timestamp alone could
            # precede commit and incorrectly imply the result was available at an earlier cutoff.
            # Retry fills a missing marker without changing any input or prediction content.
            await conn.execute(
                """UPDATE prediction.research_predictions p SET available_at = clock_timestamp()
                   FROM prediction.research_opportunities o
                   WHERE p.opportunity_id = o.opportunity_id
                     AND o.context_id = $1 AND o.capture_version = $2
                     AND p.predictor_id = 'KG_LIVE_CAPTURE' AND p.available_at IS NULL""",
                opportunity.context_id, CAPTURE_VERSION,
            )
        return inserted is not None


def build_opportunity(
    record: ContextRecord,
    events: list[ContextEvent],
    evidence: list[EventEvidence],
    edges: list[FiringEdge],
    elevated: bool,
    decision: Decision | None,
    cutoff: datetime,
    policy: dict[str, float],
    *,
    graph_failed: bool = False,
) -> CapturedOpportunity:
    """Freeze observed evidence without claiming historical market features or finalized labels."""
    if cutoff.tzinfo is None or cutoff.utcoffset() is None:
        raise ValueError("feature cutoff must be timezone-aware")
    cutoff = cutoff.astimezone(UTC)
    reasons: set[str] = set()
    news: list[dict[str, object]] = []
    for event in sorted(events, key=lambda item: str(item.event_id)):
        versions = [item for item in evidence if item.event_id == event.event_id]
        if len(versions) != 1:
            # Context membership does not yet pin revision hashes. Never silently select one.
            reasons.add("MISSING_EVENT_RECEIPT" if not versions else "AMBIGUOUS_EVENT_REVISION")
            continue
        version = versions[0]
        if content_hash(version.payload) != version.content_hash:
            reasons.add("EVENT_HASH_MISMATCH")
            continue
        raw: dict[str, Any] = json.loads(version.payload)
        if (
            version.received_at > cutoff or event.first_seen_at > cutoff
            or datetime.fromisoformat(str(raw["last_seen_at"]).replace("Z", "+00:00")) > cutoff
        ):
            reasons.add("FUTURE_EVENT_EVIDENCE")
            continue
        if (
            raw["event_type"] != event.event_type.value
            or raw["polarity"] != event.polarity.value
            or sorted(raw.get("context_tags", [])) != sorted(t.value for t in event.context_tags)
            or datetime.fromisoformat(str(raw["first_seen_at"]).replace("Z", "+00:00"))
            != event.first_seen_at
            or record.asset_id.value not in raw["affected_asset_ids"]
        ):
            reasons.add("CONTEXT_EVENT_MISMATCH")
            continue
        news.append({
            "event_id": str(event.event_id), "content_hash": version.content_hash,
            "received_at": version.received_at.isoformat(), "payload": raw,
        })
    if not events:
        reasons.add("EMPTY_CONTEXT")
    graph = [edge.model_dump(mode="json") for edge in sorted(edges, key=lambda e: e.edge_id)]
    snapshot = canonical_json({
        "capture_version": CAPTURE_VERSION,
        "feature_cutoff": cutoff.isoformat(),
        "context_id": str(record.context_id), "context_version": record.context_version,
        "asset_id": record.asset_id.value,
        "window_start": record.window_start.isoformat(),
        "window_end": record.window_end.isoformat(),
        "context_event_ids": sorted(str(event.event_id) for event in events),
        "news": news, "firing_edges": graph, "graph_hash": content_hash(canonical_json(graph)),
        "graph_policy": policy, "graph_policy_hash": content_hash(canonical_json(policy)),
        "scope_b_elevated": elevated,
        "market_feature_status": "NOT_CAPTURED",
        "training_eligible": False,
        "quality_reasons": sorted(reasons),
    })
    status: Literal["PREDICTED", "ABSTAINED", "FAILED"] = (
        "FAILED" if graph_failed else "ABSTAINED" if decision is None else "PREDICTED"
    )
    result = canonical_json({
        "status": status,
        "direction": decision.direction.value if decision else None,
        "kg_strength": decision.confidence if decision else None,
        "magnitude": decision.magnitude.value if decision else None,
        "probabilities": None,
        "reason": "GRAPH_UNAVAILABLE" if graph_failed else "NO_FIRING_DECISION"
        if decision is None else None,
    })
    return CapturedOpportunity(
        uuid.uuid5(NAMESPACE, f"{record.context_id}|{CAPTURE_VERSION}"), record.context_id,
        record.context_version, record.asset_id.value, cutoff, snapshot, content_hash(snapshot),
        "INVALID_INPUT" if reasons else "VALID", status, result,
    )


class ResearchRecorder:
    """Bound research I/O; capture errors cannot silence official KG notifications."""

    def __init__(self, store: EvidenceStore, settings: PredictionSettings) -> None:
        self._store = store
        self._timeout = settings.research_capture_timeout_seconds
        self._policy = {
            "deadband": settings.decision_deadband,
            "small_max": settings.magnitude_small_max,
            "medium_max": settings.magnitude_medium_max,
            "evidence_halfpoint": settings.confidence_evidence_halfpoint,
        }
        self.captured = 0
        self.failures = 0
        self.invalid = 0
        self.last_success_at: datetime | None = None

    async def record_event(self, event: EventDetected) -> None:
        try:
            async with asyncio.timeout(self._timeout):
                await self._store.record_event(event)
        except Exception:  # noqa: BLE001 - research failure must not interrupt official processing
            self.failures += 1
            logger.warning("research_event_capture_failed", event_id=str(event.event_id))

    async def capture(
        self, record: ContextRecord, events: list[ContextEvent], edges: list[FiringEdge],
        elevated: bool, decision: Decision | None, cutoff: datetime,
        *, graph_failed: bool = False,
    ) -> None:
        try:
            async with asyncio.timeout(self._timeout):
                evidence = await self._store.load_events(
                    [event.event_id for event in events], cutoff,
                )
                opportunity = build_opportunity(
                    record, events, evidence, edges, elevated, decision, cutoff, self._policy,
                    graph_failed=graph_failed,
                )
                if await self._store.save(opportunity):
                    self.captured += 1
                    self.invalid += opportunity.quality == "INVALID_INPUT"
                self.last_success_at = datetime.now(UTC)
        except Exception:  # noqa: BLE001 - health exposes missing research evidence
            self.failures += 1
            logger.warning("research_opportunity_capture_failed", context_id=str(record.context_id))
