from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from shared.schemas.messages import ArticleIngested, EventDetected, EventType, ExtractionMethod

from cleansing.clustering import ClusterCandidate, update_centroid
from cleansing.config import CleansingSettings
from cleansing.embedding import cosine_similarity
from cleansing.extraction import KeywordExtractor
from cleansing.models import ArticleFacts, ClusterRecord, ClusterState, ExtractedAction
from cleansing.pipeline import CleansingPipeline


class ConstantEmbedder:
    """Deterministic embedder returning the same unit vector for all text.

    This isolates Gate 2 (taxonomy) in clustering tests: every pair is maximally similar, so only
    the canonical event type decides whether articles merge.
    """

    def __init__(self, dimension: int = 8) -> None:
        self._dimension = dimension

    @property
    def dimension(self) -> int:
        return self._dimension

    def is_ready(self) -> bool:
        return True

    async def embed(self, text: str) -> list[float]:
        vector = [0.0] * self._dimension
        vector[0] = 1.0
        return vector


class FakeRepository:
    """In-memory PipelineRepository for unit tests."""

    def __init__(self) -> None:
        self.fingerprints: dict[uuid.UUID, int] = {}
        self.embeddings: dict[uuid.UUID, list[float]] = {}
        self.actions: dict[uuid.UUID, ExtractedAction] = {}
        self.clusters: dict[uuid.UUID, ClusterRecord] = {}
        self.members: dict[uuid.UUID, list[dict[str, Any]]] = {}
        self.events: list[EventDetected] = []

    async def article_already_processed(self, article_id: uuid.UUID) -> bool:
        return article_id in self.fingerprints

    async def recent_fingerprints(self, since: datetime) -> list[int]:
        return list(self.fingerprints.values())

    async def store_fingerprint(
        self, article_id: uuid.UUID, simhash: int, source_id: str, published_at: datetime
    ) -> None:
        self.fingerprints[article_id] = simhash

    async def store_embedding(self, article_id: uuid.UUID, vector: list[float]) -> None:
        self.embeddings[article_id] = vector

    async def store_action(
        self, article_id: uuid.UUID, action: ExtractedAction, language: str
    ) -> None:
        self.actions[article_id] = action

    async def find_candidate_clusters(
        self, vector: list[float], event_type: EventType, *, limit: int = 5
    ) -> list[ClusterCandidate]:
        candidates: list[ClusterCandidate] = []
        for cluster in self.clusters.values():
            if cluster.state not in {ClusterState.OPEN, ClusterState.QUIET}:
                continue
            if cluster.event_type != event_type:
                continue
            candidates.append(
                ClusterCandidate(
                    cluster_id=cluster.cluster_id,
                    event_type=cluster.event_type,
                    similarity=cosine_similarity(vector, cluster.centroid),
                )
            )
        candidates.sort(key=lambda c: c.similarity, reverse=True)
        return candidates[:limit]

    def _add_member(self, cluster_id: uuid.UUID, facts: ArticleFacts) -> None:
        self.members.setdefault(cluster_id, []).append(
            {
                "article_id": facts.article_id,
                "title": facts.title,
                "source_id": facts.source_id,
                "canonical_url": facts.canonical_url,
                "published_at": facts.published_at,
                "correlation_id": facts.correlation_id,
            }
        )

    async def create_cluster(
        self,
        facts: ArticleFacts,
        event_type: EventType,
        vector: list[float],
        *,
        quiet_deadline: datetime,
        lifetime_deadline: datetime,
    ) -> uuid.UUID:
        cluster_id = uuid.uuid4()
        self.clusters[cluster_id] = ClusterRecord(
            cluster_id=cluster_id,
            event_type=event_type,
            state=ClusterState.OPEN,
            article_count=1,
            first_seen_at=facts.published_at,
            last_seen_at=facts.published_at,
            quiet_deadline=quiet_deadline,
            lifetime_deadline=lifetime_deadline,
            centroid=list(vector),
        )
        self._add_member(cluster_id, facts)
        return cluster_id

    async def add_article_to_cluster(
        self,
        cluster_id: uuid.UUID,
        facts: ArticleFacts,
        article_vector: list[float],
        *,
        quiet_deadline: datetime,
    ) -> None:
        cluster = self.clusters[cluster_id]
        cluster.centroid = update_centroid(
            cluster.centroid, cluster.article_count, article_vector
        )
        cluster.article_count += 1
        cluster.last_seen_at = facts.published_at
        cluster.quiet_deadline = quiet_deadline
        cluster.state = ClusterState.OPEN
        self._add_member(cluster_id, facts)

    async def claim_ready_clusters(
        self, now: datetime, *, limit: int = 20
    ) -> list[ClusterRecord]:
        claimed: list[ClusterRecord] = []
        for cluster in self.clusters.values():
            if cluster.state not in {ClusterState.OPEN, ClusterState.QUIET}:
                continue
            if cluster.quiet_deadline <= now or cluster.lifetime_deadline <= now:
                cluster.state = ClusterState.MERGING
                claimed.append(cluster)
        return claimed[:limit]

    async def load_cluster_articles(self, cluster_id: uuid.UUID) -> list[Any]:
        return self.members.get(cluster_id, [])

    async def load_cluster_actions(self, cluster_id: uuid.UUID) -> list[Any]:
        rows: list[dict[str, Any]] = []
        for member in self.members.get(cluster_id, []):
            action = self.actions[member["article_id"]]
            rows.append(
                {
                    "article_id": member["article_id"],
                    "actor": action.actor,
                    "action_lemma": action.action_lemma,
                    "object": action.object,
                    "event_type": action.event_type.value,
                    "affected_asset_ids": [a.value for a in action.affected_asset_ids],
                    "polarity": action.polarity.value,
                    "context_tags": [c.value for c in action.context_tags],
                }
            )
        return rows

    async def set_cluster_state(self, cluster_id: uuid.UUID, state: ClusterState) -> None:
        self.clusters[cluster_id].state = state

    async def store_event_with_outbox(self, event: EventDetected) -> None:
        self.events.append(event)
        self.clusters[event.cluster_id].state = ClusterState.MERGED


def _message(
    title: str, body: str, *, language: str = "en", country: str = "US"
) -> ArticleIngested:
    return ArticleIngested(
        correlation_id=uuid.uuid4(),
        occurred_at=datetime.now(UTC),
        article_id=uuid.uuid4(),
        source_id="reuters",
        canonical_url=f"https://example.com/{uuid.uuid4()}",
        title=title,
        body=body,
        published_at=datetime.now(UTC),
        language=language,
        country=country,
        content_hash=uuid.uuid4().hex,
    )


def _pipeline(repo: FakeRepository) -> CleansingPipeline:
    return CleansingPipeline(repo, ConstantEmbedder(), KeywordExtractor(), CleansingSettings())


async def test_near_duplicate_is_dropped() -> None:
    repo = FakeRepository()
    pipeline = _pipeline(repo)
    body = "OPEC agrees to cut oil output by two million barrels per day starting next month"
    await pipeline.process_article(_message("OPEC cuts output", body))
    await pipeline.process_article(_message("OPEC cuts output", body + "."))
    # Only the first copy created a fingerprint and a cluster.
    assert len(repo.fingerprints) == 1
    assert len(repo.clusters) == 1


async def test_distinct_event_types_stay_separate() -> None:
    repo = FakeRepository()
    pipeline = _pipeline(repo)
    # Identical embeddings (ConstantEmbedder) but different canonical types -> Gate 2 keeps apart.
    await pipeline.process_article(_message("War", "Missiles strike the capital in a fresh attack"))
    await pipeline.process_article(_message("Strait", "Navy moves to block the strait passage"))
    assert len(repo.clusters) == 2
    types = {c.event_type for c in repo.clusters.values()}
    assert types == {EventType.MILITARY_CONFLICT, EventType.STRAIT_CLOSURE}


async def test_cross_language_same_event_merges() -> None:
    repo = FakeRepository()
    pipeline = _pipeline(repo)
    await pipeline.process_article(
        _message("Rate", "The central bank will raise interest rate this week")
    )
    await pipeline.process_article(
        _message("Ränta", "Riksbanken höjer ränta med 25 punkter", language="sv", country="SE")
    )
    # Both map to RATE_DECISION and (with identical embeddings) join one cluster.
    assert len(repo.clusters) == 1
    only = next(iter(repo.clusters.values()))
    assert only.event_type == EventType.RATE_DECISION
    assert only.article_count == 2


async def test_article_count_does_not_close_cluster() -> None:
    repo = FakeRepository()
    pipeline = _pipeline(repo)
    await pipeline.process_article(_message("S1", "US imposes sanctions on the exporter"))
    await pipeline.process_article(_message("S2", "US imposes sanctions on the exporter again"))
    produced = await pipeline.close_ready_clusters()
    # Two articles, but the quiet period has not elapsed -> nothing closes yet.
    assert produced == 0
    assert repo.events == []


async def test_replayed_article_is_idempotent() -> None:
    repo = FakeRepository()
    pipeline = _pipeline(repo)
    message = _message("S1", "US imposes sanctions on the exporter")
    await pipeline.process_article(message)
    await pipeline.process_article(message)
    assert len(repo.fingerprints) == 1
    assert len(repo.clusters) == 1


async def test_ready_cluster_produces_local_event() -> None:
    repo = FakeRepository()
    pipeline = _pipeline(repo)
    await pipeline.process_article(_message("S1", "US imposes sanctions on the exporter"))
    # Force the quiet deadline into the past so the cluster is due.
    cluster = next(iter(repo.clusters.values()))
    cluster.quiet_deadline = datetime.now(UTC) - timedelta(minutes=1)

    produced = await pipeline.close_ready_clusters()
    assert produced == 1
    assert len(repo.events) == 1
    event = repo.events[0]
    assert event.extraction_method == ExtractionMethod.LOCAL
    assert event.event_type == EventType.SANCTIONS
    assert event.llm_metadata is None
    assert cluster.state == ClusterState.MERGED
