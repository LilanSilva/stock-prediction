"""Cleansing pipeline orchestration.

Two responsibilities:

  - `process_article`: dedup -> embed -> extract -> map -> conservative dual-gate cluster assignment
    for a single `ArticleIngested` message. Idempotent by `article_id`.
  - `close_ready_clusters`: time-triggered closing of due clusters into exactly one `EventDetected`
    each, local-first with an LLM fallback only for ambiguous/conflicting clusters.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import structlog
from shared.schemas.messages import ArticleIngested, EventDetected, EventType

from cleansing.clustering import (
    ClusterCandidate,
    decide_assignment,
    lifetime_deadline,
    quiet_deadline,
)
from cleansing.config import CleansingSettings
from cleansing.dedup import is_near_duplicate, simhash
from cleansing.embedding import Embedder
from cleansing.exceptions import AmbiguousMergeError
from cleansing.extraction import ActionExtractor
from cleansing.merge import ClusterInputs, LlmMerger, build_local_event, detect_fact_conflicts
from cleansing.models import ArticleFacts, ClusterRecord, ClusterState, ExtractedAction

logger = structlog.get_logger(__name__)


class PipelineRepository(Protocol):
    """Structural type for the persistence operations the pipeline needs (repository.py provides
    the concrete implementation; tests provide an in-memory fake)."""

    async def article_already_processed(self, article_id: uuid.UUID) -> bool: ...

    async def recent_fingerprints(self, since: datetime) -> list[int]: ...

    async def store_fingerprint(
        self, article_id: uuid.UUID, simhash: int, source_id: str, published_at: datetime
    ) -> None: ...

    async def store_embedding(self, article_id: uuid.UUID, vector: list[float]) -> None: ...

    async def store_action(
        self, article_id: uuid.UUID, action: ExtractedAction, language: str
    ) -> None: ...

    async def find_candidate_clusters(
        self, vector: list[float], event_type: EventType, *, limit: int = 5
    ) -> list[ClusterCandidate]: ...

    async def create_cluster(
        self,
        facts: ArticleFacts,
        event_type: EventType,
        vector: list[float],
        *,
        quiet_deadline: datetime,
        lifetime_deadline: datetime,
    ) -> uuid.UUID: ...

    async def add_article_to_cluster(
        self,
        cluster_id: uuid.UUID,
        facts: ArticleFacts,
        article_vector: list[float],
        *,
        quiet_deadline: datetime,
    ) -> None: ...

    async def claim_ready_clusters(
        self, now: datetime, *, limit: int = 20
    ) -> list[ClusterRecord]: ...

    async def load_cluster_articles(self, cluster_id: uuid.UUID) -> list[Any]: ...

    async def load_cluster_actions(self, cluster_id: uuid.UUID) -> list[Any]: ...

    async def set_cluster_state(self, cluster_id: uuid.UUID, state: ClusterState) -> None: ...

    async def store_event_with_outbox(self, event: EventDetected) -> None: ...


def _facts_from_message(message: ArticleIngested) -> ArticleFacts:
    return ArticleFacts(
        article_id=message.article_id,
        source_id=message.source_id,
        canonical_url=str(message.canonical_url),
        title=message.title,
        body=message.body,
        language=message.language,
        country=message.country,
        published_at=message.published_at,
        correlation_id=message.correlation_id,
    )


class CleansingPipeline:
    def __init__(
        self,
        repository: PipelineRepository,
        embedder: Embedder,
        extractor: ActionExtractor,
        settings: CleansingSettings,
        *,
        llm_merger: LlmMerger | None = None,
    ) -> None:
        self._repo = repository
        self._embedder = embedder
        self._extractor = extractor
        self._settings = settings
        self._llm_merger = llm_merger

    async def process_article(self, message: ArticleIngested) -> None:
        """Process one article: dedup, embed, extract, and assign to a cluster (idempotent)."""
        facts = _facts_from_message(message)
        if await self._repo.article_already_processed(facts.article_id):
            logger.info("article_replay_skipped", article_id=str(facts.article_id))
            return

        text = f"{facts.title}\n{facts.body}"
        fingerprint = simhash(text)

        window_start = datetime.now(UTC) - timedelta(hours=self._settings.dedup_window_hours)
        recent = await self._repo.recent_fingerprints(window_start)
        if any(
            is_near_duplicate(fingerprint, existing, self._settings.simhash_max_distance)
            for existing in recent
        ):
            logger.info("near_duplicate_dropped", article_id=str(facts.article_id))
            return

        vector = await self._embedder.embed(text)
        action = await self._extractor.extract(text, facts.language)

        await self._repo.store_fingerprint(
            facts.article_id, fingerprint, facts.source_id, facts.published_at
        )
        await self._repo.store_embedding(facts.article_id, vector)
        await self._repo.store_action(facts.article_id, action, facts.language)

        await self._assign_to_cluster(facts, action.event_type, vector)

    async def _assign_to_cluster(
        self, facts: ArticleFacts, event_type: EventType, vector: list[float]
    ) -> None:
        now = datetime.now(UTC)
        quiet_at = quiet_deadline(now, self._settings.quiet_period_minutes)
        lifetime_at = lifetime_deadline(now, self._settings.max_lifetime_hours)

        candidates: list[ClusterCandidate] = []
        if event_type != EventType.OTHER:
            candidates = await self._repo.find_candidate_clusters(vector, event_type)
        decision = decide_assignment(
            event_type, candidates, similarity_threshold=self._settings.similarity_threshold
        )

        if decision.cluster_id is None:
            cluster_id = await self._repo.create_cluster(
                facts,
                event_type,
                vector,
                quiet_deadline=quiet_at,
                lifetime_deadline=lifetime_at,
            )
            logger.info(
                "cluster_created",
                cluster_id=str(cluster_id),
                event_type=event_type.value,
                article_id=str(facts.article_id),
            )
            return

        await self._repo.add_article_to_cluster(
            decision.cluster_id, facts, vector, quiet_deadline=quiet_at
        )
        logger.info(
            "cluster_joined",
            cluster_id=str(decision.cluster_id),
            event_type=event_type.value,
            article_id=str(facts.article_id),
        )

    async def close_ready_clusters(self) -> int:
        """Close all due clusters into events. Returns the number of events produced."""
        now = datetime.now(UTC)
        claimed = await self._repo.claim_ready_clusters(now)
        produced = 0
        for record in claimed:
            articles = await self._repo.load_cluster_articles(record.cluster_id)
            actions = await self._repo.load_cluster_actions(record.cluster_id)
            inputs = ClusterInputs(record=record, articles=articles, actions=actions)
            conflicts = detect_fact_conflicts(actions)

            try:
                if conflicts:
                    if self._llm_merger is None:
                        raise AmbiguousMergeError("ambiguous cluster and no LLM merger configured")
                    event = await self._llm_merger.merge(inputs)
                else:
                    event = build_local_event(inputs)
            except AmbiguousMergeError as exc:
                logger.warning(
                    "cluster_merge_deferred",
                    cluster_id=str(record.cluster_id),
                    error=str(exc),
                )
                await self._repo.set_cluster_state(
                    record.cluster_id, ClusterState.ERROR_RETRYABLE
                )
                continue

            await self._repo.store_event_with_outbox(event)
            produced += 1
            logger.info(
                "event_detected",
                cluster_id=str(record.cluster_id),
                event_id=str(event.event_id),
                event_type=event.event_type.value,
                extraction_method=event.extraction_method.value,
                source_count=len(event.sources),
            )
        return produced
