import asyncio
import uuid
from datetime import UTC, datetime

from shared.schemas.messages import ArticleIngested

from ingestion.fetcher import BodyFetcher
from ingestion.models import RawArticle
from ingestion.pipeline import IngestionPipeline


def _raw(url: str, summary: str = "fallback summary") -> RawArticle:
    return RawArticle(
        source_id="di",
        url=url,
        title="Title",
        summary=summary,
        published_at=datetime(2026, 7, 22, 8, 30, tzinfo=UTC),
        language="sv",
        country="SE",
    )


class _RecordingAdapter:
    def __init__(self, articles: list[RawArticle]) -> None:
        self._articles = articles

    @property
    def source_id(self) -> str:
        return "di"

    async def fetch(self) -> list[RawArticle]:
        return self._articles


class _CapturingStore:
    def __init__(self) -> None:
        self.bodies: dict[str, str] = {}

    async def store_new_article(
        self,
        raw: RawArticle,
        body: str,
        *,
        correlation_id: uuid.UUID,
        max_body_chars: int = 2000,
    ) -> ArticleIngested | None:
        self.bodies[str(raw.url)] = body
        return None


class _NoopOutbox:
    async def publish_pending(self, *, batch_size: int = 100) -> int:
        return 0


class _CountingBodyFetcher(BodyFetcher):
    """A BodyFetcher stand-in that tracks concurrency and can fail/hang specific URLs."""

    def __init__(
        self, *, fail_urls: set[str] | None = None, hang_urls: set[str] | None = None
    ) -> None:
        self._fail_urls = fail_urls or set()
        self._hang_urls = hang_urls or set()
        self.in_flight = 0
        self.max_in_flight = 0

    async def fetch(self, url: str) -> str:
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(0)  # yield so overlaps are observable
            if url in self._hang_urls:
                await asyncio.sleep(0.2)
            if url in self._fail_urls:
                raise RuntimeError("boom")  # non-BodyFetchError: must still be isolated
            return f"body::{url}"
        finally:
            self.in_flight -= 1


async def test_bodies_fetched_concurrently_up_to_limit() -> None:
    raws = [_raw(f"https://www.di.se/a{i}") for i in range(10)]
    fetcher = _CountingBodyFetcher()
    store = _CapturingStore()
    pipeline = IngestionPipeline(
        [_RecordingAdapter(raws)], store, _NoopOutbox(),
        body_fetcher=fetcher, body_fetch_concurrency=3,
    )

    await pipeline.run_once()

    assert len(store.bodies) == 10
    assert fetcher.max_in_flight <= 3  # concurrency cap respected
    assert fetcher.max_in_flight > 1  # actually ran in parallel


async def test_one_failing_fetch_does_not_break_batch() -> None:
    raws = [_raw("https://www.di.se/ok1"), _raw("https://www.di.se/bad"), _raw("https://www.di.se/ok2")]
    fetcher = _CountingBodyFetcher(fail_urls={"https://www.di.se/bad"})
    store = _CapturingStore()
    pipeline = IngestionPipeline(
        [_RecordingAdapter(raws)], store, _NoopOutbox(),
        body_fetcher=fetcher, body_fetch_concurrency=5,
    )

    await pipeline.run_once()

    # All three still processed; the failing one fell back to its summary.
    assert len(store.bodies) == 3
    assert store.bodies["https://www.di.se/bad"] == "fallback summary"
    assert store.bodies["https://www.di.se/ok1"] == "body::https://www.di.se/ok1"


async def test_slow_fetch_does_not_block_others() -> None:
    raws = [_raw("https://www.di.se/slow"), _raw("https://www.di.se/fast")]
    fetcher = _CountingBodyFetcher(hang_urls={"https://www.di.se/slow"})
    store = _CapturingStore()
    pipeline = IngestionPipeline(
        [_RecordingAdapter(raws)], store, _NoopOutbox(),
        body_fetcher=fetcher, body_fetch_concurrency=5,
    )

    await pipeline.run_once()
    assert len(store.bodies) == 2
