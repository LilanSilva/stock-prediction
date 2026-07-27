import uuid
from datetime import UTC, datetime

from shared.schemas.messages import ArticleIngested

from ingestion.exceptions import AdapterError
from ingestion.models import RawArticle
from ingestion.pipeline import IngestionPipeline


def _raw(url: str) -> RawArticle:
    return RawArticle(
        source_id="di",
        url=url,
        title="Oljepriset stiger",
        summary="Brent stiger.",
        published_at=datetime(2026, 7, 22, 8, 30, tzinfo=UTC),
        language="sv",
        country="SE",
    )


class FakeAdapter:
    def __init__(self, source_id: str, articles: list[RawArticle], fail: bool = False) -> None:
        self._source_id = source_id
        self._articles = articles
        self._fail = fail

    @property
    def source_id(self) -> str:
        return self._source_id

    async def fetch(self) -> list[RawArticle]:
        if self._fail:
            raise AdapterError("boom")
        return self._articles


class FakeStore:
    def __init__(self) -> None:
        self.seen: set[str] = set()

    async def store_new_article(
        self,
        raw: RawArticle,
        body: str,
        *,
        correlation_id: uuid.UUID,
        max_body_chars: int = 2000,
    ) -> ArticleIngested | None:
        key = str(raw.url)
        if key in self.seen:
            return None
        self.seen.add(key)
        return ArticleIngested(
            correlation_id=correlation_id,
            occurred_at=datetime.now(UTC),
            article_id=uuid.uuid4(),
            source_id=raw.source_id,
            canonical_url=str(raw.url),
            title=raw.title,
            body=body,
            published_at=raw.published_at,
            language=raw.language,
            country=raw.country,
            content_hash="abc",
        )


class FakeOutbox:
    def __init__(self) -> None:
        self.calls = 0

    async def publish_pending(self, *, batch_size: int = 100) -> int:
        self.calls += 1
        return 0


async def test_pipeline_counts_new_articles_and_skips_duplicates() -> None:
    articles = [_raw("https://www.di.se/a"), _raw("https://www.di.se/a"), _raw("https://www.di.se/b")]
    store = FakeStore()
    outbox = FakeOutbox()
    pipeline = IngestionPipeline(
        [FakeAdapter("di", articles)], store, outbox, body_fetch_enabled=False
    )

    result = await pipeline.run_once()

    # Two unique URLs stored; the duplicate is skipped.
    assert result.per_source_counts == {"di": 2}
    assert result.total_new == 2
    assert outbox.calls == 1


async def test_failing_source_does_not_abort_others() -> None:
    good = FakeAdapter("dn", [_raw("https://www.dn.se/x")])
    bad = FakeAdapter("di", [], fail=True)
    pipeline = IngestionPipeline([bad, good], FakeStore(), FakeOutbox(), body_fetch_enabled=False)

    result = await pipeline.run_once()

    assert result.per_source_counts == {"di": 0, "dn": 1}
