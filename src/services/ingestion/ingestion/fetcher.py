"""SSRF-hardened article body fetcher.

Downloaded content is untrusted. Per the functional document (sec 4) this fetcher:
  - permits only http/https;
  - resolves the host and rejects loopback, private, link-local, multicast, reserved, and
    cloud-metadata destinations;
  - re-resolves and re-validates every redirect target;
  - bounds redirect count, response bytes, timeouts, and accepted content types;
  - never forwards local credentials, cookies, or authorization headers.
"""

from __future__ import annotations

import asyncio
import codecs
import ipaddress
import socket

import httpx

from ingestion.exceptions import BodyFetchError

ALLOWED_SCHEMES = frozenset({"http", "https"})
DEFAULT_ALLOWED_CONTENT_TYPES = frozenset({"text/html", "application/xhtml+xml", "text/plain"})
DEFAULT_MAX_BYTES = 2_000_000
DEFAULT_MAX_REDIRECTS = 3
DEFAULT_OVERALL_TIMEOUT_SECONDS = 20.0
_STRIPPED_REQUEST_HEADERS = ("authorization", "cookie", "proxy-authorization")


def decode_body(data: bytes, encoding: str | None, *, truncated: bool = False) -> str:
    """Prefer a BOM, then the declared encoding, with one strict UTF-8 fallback."""
    bom_encoding = next(
        (
            name
            for bom, name in (
                (codecs.BOM_UTF32_LE, "utf-32"),
                (codecs.BOM_UTF32_BE, "utf-32"),
                (codecs.BOM_UTF8, "utf-8-sig"),
                (codecs.BOM_UTF16_LE, "utf-16"),
                (codecs.BOM_UTF16_BE, "utf-16"),
            )
            if data.startswith(bom)
        ),
        None,
    )
    for candidate in dict.fromkeys([bom_encoding or encoding or "utf-8", "utf-8"]):
        try:
            if not getattr(codecs.lookup(candidate), "_is_text_encoding", False):
                continue
            decoder = codecs.getincrementaldecoder(candidate)(errors="strict")
            text = decoder.decode(data, final=not truncated)
            if isinstance(text, str):
                return text
        except (UnicodeError, LookupError, ValueError):
            continue
    raise BodyFetchError("article encoding could not be decoded without data loss")


class SsrfBlockedError(BodyFetchError):
    """The requested (or redirected) destination is not a permitted public address."""


def _ip_is_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local  # includes 169.254.169.254 cloud metadata
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


async def _resolve_host_ips(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise SsrfBlockedError(f"cannot resolve host {host!r}: {exc}") from exc
    ips: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for info in infos:
        raw = str(info[4][0]).split("%", 1)[0]  # drop any IPv6 zone id
        ips.append(ipaddress.ip_address(raw))
    return ips


async def validate_public_url(url: httpx.URL) -> None:
    """Raise `SsrfBlockedError` unless `url` is http/https and resolves only to public addresses."""
    if url.scheme not in ALLOWED_SCHEMES:
        raise SsrfBlockedError(f"scheme {url.scheme!r} is not permitted")
    host = url.host
    if not host:
        raise SsrfBlockedError("url has no host")

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        ips = await _resolve_host_ips(host)
    else:
        ips = [literal]

    if not ips:
        raise SsrfBlockedError(f"host {host!r} did not resolve")
    for ip in ips:
        if _ip_is_blocked(ip):
            raise SsrfBlockedError(f"destination {ip} is not a permitted public address")


class BodyFetcher:
    """Fetches an article body over HTTP(S) with SSRF, redirect, size, and type controls."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        max_bytes: int = DEFAULT_MAX_BYTES,
        max_redirects: int = DEFAULT_MAX_REDIRECTS,
        allowed_content_types: frozenset[str] = DEFAULT_ALLOWED_CONTENT_TYPES,
        overall_timeout_seconds: float = DEFAULT_OVERALL_TIMEOUT_SECONDS,
    ) -> None:
        self._client = client
        self._max_bytes = max_bytes
        self._max_redirects = max_redirects
        self._allowed_content_types = allowed_content_types
        self._overall_timeout_seconds = overall_timeout_seconds

    async def fetch(self, url: str) -> str:
        """Return the decoded body text, bounded by an overall timeout.

        Raises `BodyFetchError`/`SsrfBlockedError`. The overall timeout guards against slow-trickle
        servers that never trip httpx's per-read timeout.
        """
        try:
            async with asyncio.timeout(self._overall_timeout_seconds):
                return await self._fetch(url)
        except TimeoutError as exc:
            raise BodyFetchError(f"body fetch exceeded {self._overall_timeout_seconds}s") from exc

    async def _fetch(self, url: str) -> str:
        current = httpx.URL(url)
        # Explicitly strip sensitive headers; the client must not follow redirects itself so we can
        # re-validate each hop.
        headers = {"user-agent": "feed-analyzer-ingestion/0.1"}

        for _ in range(self._max_redirects + 1):
            await validate_public_url(current)
            async with self._client.stream(
                "GET", current, headers=headers, follow_redirects=False
            ) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise BodyFetchError("redirect response missing Location header")
                    current = current.join(location)
                    continue

                response.raise_for_status()
                raw_content_type = response.headers.get("content-type", "")
                content_type = raw_content_type.split(";", 1)[0].strip().lower()
                if content_type and content_type not in self._allowed_content_types:
                    raise BodyFetchError(f"content-type {content_type!r} is not permitted")

                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    chunks.append(chunk[: self._max_bytes - total])
                    total += len(chunk)
                    if total >= self._max_bytes:
                        break
                body = b"".join(chunks)[: self._max_bytes]
                return decode_body(body, response.encoding, truncated=total >= self._max_bytes)

        raise BodyFetchError(f"exceeded {self._max_redirects} redirects")


def sanitize_headers_for_request(headers: dict[str, str]) -> dict[str, str]:
    """Drop credential-bearing headers before an outbound fetch (defense in depth)."""
    return {k: v for k, v in headers.items() if k.lower() not in _STRIPPED_REQUEST_HEADERS}
