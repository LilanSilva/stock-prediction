"""Hourly ingestion pipeline orchestration.

Polls every configured source, fetches bodies (falling back to the feed summary on failure),
stores new articles with their outbox rows, and relays the outbox to `feed.events`. A failure in one
source never aborts the others (functional document sec 7).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import structlog
from shared.schemas.messages import ArticleIngested

from ingestion.exceptions import AdapterError
from ingestion.fetcher import BodyFetcher
from ingestion.models import RawArticle
from ingestion.normalize import strip_html_page

logger = structlog.get_logger(__name__)


class SourceAdapter(Protocol):
    """Structural type for a source adapter."""

    @property
    def source_id(self) -> str: ...

    async def fetch(self) -> list[RawArticle]: ...


class ArticleStore(Protocol):
    async def store_new_article(
        self,
        raw: RawArticle,
        body: str,
        *,
        correlation_id: uuid.UUID,
        max_body_chars: int = 2000,
    ) -> ArticleIngested | None: ...


class OutboxRelay(Protocol):
    async def publish_pending(self, *, batch_size: int = 100) -> int: ...


@dataclass(frozen=True)
class PollResult:
    correlation_id: str
    per_source_counts: dict[str, int]
    published: int

    @property
    def total_new(self) -> int:
        return sum(self.per_source_counts.values())


class IngestionPipeline:
    def __init__(
        self,
        adapters: Sequence[SourceAdapter],
        repository: ArticleStore,
        outbox_publisher: OutboxRelay,
        *,
        body_fetcher: BodyFetcher | None = None,
        body_fetch_enabled: bool = True,
        body_fetch_concurrency: int = 10,
        max_body_chars: int = 2000,
    ) -> None:
        self._adapters = adapters
        self._repository = repository
        self._outbox_publisher = outbox_publisher
        self._body_fetcher = body_fetcher
        self._body_fetch_enabled = body_fetch_enabled
        self._body_fetch_concurrency = max(1, body_fetch_concurrency)
        self._max_body_chars = max_body_chars

    async def run_once(self) -> PollResult:
        poll_id = uuid.uuid4()
        counts: dict[str, int] = {}

        for adapter in self._adapters:
            try:
                raws = await adapter.fetch()
            except AdapterError as exc:
                logger.warning("source_fetch_failed", source_id=adapter.source_id, error=str(exc))
                counts[adapter.source_id] = 0
                continue

            # Fetch bodies concurrently (bounded), then store sequentially. A broken or slow body
            # fetch degrades only its own article (falls back to the summary), never the batch.
            fetched = await self._fetch_bodies(raws)
            stored = 0
            for raw, body in fetched:
                try:
                    # Each ArticleIngested is the root of its own causal chain, so it gets a fresh
                    # correlation_id (propagated downstream to the event it later causes).
                    message = await self._repository.store_new_article(
                        raw,
                        body,
                        correlation_id=uuid.uuid4(),
                        max_body_chars=self._max_body_chars,
                    )
                except Exception as exc:  # noqa: BLE001 - one bad row must not abort the source
                    logger.error(
                        "article_store_failed", source_id=adapter.source_id, error=str(exc)
                    )
                    continue
                if message is not None:
                    stored += 1
            counts[adapter.source_id] = stored

        published = await self._outbox_publisher.publish_pending()
        result = PollResult(str(poll_id), counts, published)
        logger.info(
            "poll_completed",
            correlation_id=result.correlation_id,
            per_source_counts=counts,
            total_new=result.total_new,
            published=published,
        )
        return result

    async def _fetch_bodies(self, raws: list[RawArticle]) -> list[tuple[RawArticle, str]]:
        """Resolve bodies for a source's articles with bounded concurrency.

        Each task uses `_resolve_body`, which never raises, so a single failing/slow fetch cannot
        fail the batch. Concurrency is capped by a semaphore to stay polite and bounded.
        """
        if not raws:
            return []
        semaphore = asyncio.Semaphore(self._body_fetch_concurrency)

        async def _one(raw: RawArticle) -> tuple[RawArticle, str]:
            async with semaphore:
                return raw, await self._resolve_body(raw)

        return await asyncio.gather(*(_one(raw) for raw in raws))

    async def _resolve_body(self, raw: RawArticle) -> str:
        """Return the article body, always falling back to the feed summary on any failure.

        This must never raise: it is the fault-isolation boundary for concurrent body fetching.
        """
        if self._body_fetcher is None or not self._body_fetch_enabled:
            return raw.summary
        try:
            fetched = await self._body_fetcher.fetch(str(raw.url))
            clean = strip_html_page(fetched)
            if not clean:
                logger.info("body_is_html_page_fallback", url=str(raw.url))
                return raw.summary
            return clean
        except Exception as exc:  # noqa: BLE001 - degrade one article, never break the batch
            logger.info("body_fetch_fallback", url=str(raw.url), error=str(exc))
            return raw.summary
