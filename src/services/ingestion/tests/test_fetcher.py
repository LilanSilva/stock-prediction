import httpx
import pytest

from ingestion.exceptions import BodyFetchError
from ingestion.fetcher import BodyFetcher, SsrfBlockedError, validate_public_url


async def test_rejects_non_http_scheme() -> None:
    with pytest.raises(SsrfBlockedError):
        await validate_public_url(httpx.URL("file:///etc/passwd"))


async def test_rejects_loopback_literal() -> None:
    with pytest.raises(SsrfBlockedError):
        await validate_public_url(httpx.URL("http://127.0.0.1/x"))


async def test_rejects_private_literal() -> None:
    with pytest.raises(SsrfBlockedError):
        await validate_public_url(httpx.URL("http://10.0.0.5/x"))


async def test_rejects_cloud_metadata_ip() -> None:
    with pytest.raises(SsrfBlockedError):
        await validate_public_url(httpx.URL("http://169.254.169.254/latest/meta-data/"))


async def test_allows_public_literal() -> None:
    # 93.184.216.34 (example.com) is a public address; must not raise.
    await validate_public_url(httpx.URL("http://93.184.216.34/"))


async def test_fetch_returns_body_for_allowed_content() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"<html>hi</html>", headers={"content-type": "text/html"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        fetcher = BodyFetcher(client)
        body = await fetcher.fetch("http://93.184.216.34/article")
    assert "hi" in body


async def test_fetch_rejects_disallowed_content_type() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"{}", headers={"content-type": "application/json"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        fetcher = BodyFetcher(client)
        with pytest.raises(BodyFetchError):
            await fetcher.fetch("http://93.184.216.34/article")


async def test_fetch_truncates_to_max_bytes() -> None:
    big = b"a" * 5000

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=big, headers={"content-type": "text/plain"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        fetcher = BodyFetcher(client, max_bytes=1000)
        body = await fetcher.fetch("http://93.184.216.34/a")
    assert len(body) <= 1000


async def test_fetch_revalidates_redirect_to_blocked_host() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/start":
            return httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})
        return httpx.Response(200, content=b"ok", headers={"content-type": "text/html"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        fetcher = BodyFetcher(client)
        with pytest.raises(SsrfBlockedError):
            await fetcher.fetch("http://93.184.216.34/start")


async def test_fetch_follows_redirect_to_allowed_host() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/start":
            return httpx.Response(302, headers={"location": "http://93.184.216.34/final"})
        return httpx.Response(200, content=b"<p>done</p>", headers={"content-type": "text/html"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        fetcher = BodyFetcher(client)
        body = await fetcher.fetch("http://93.184.216.34/start")
    assert "done" in body
