"""HTTP client using the operating system's trusted certificate authorities."""

from __future__ import annotations

import ssl

from httpx import AsyncClient


def create_http_client(timeout: float = 30.0) -> AsyncClient:
    """Keep TLS verification enabled, including Windows roots and OpenSSL CA overrides."""
    return AsyncClient(verify=ssl.create_default_context(), timeout=timeout)
