"""Certificate trust regressions without contacting an external provider."""

from __future__ import annotations

import ssl
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from notification.http_client import create_http_client


async def test_client_uses_system_roots_with_verification_enabled() -> None:
    with patch("notification.http_client.AsyncClient", wraps=httpx.AsyncClient) as factory:
        async with create_http_client(timeout=7.0) as client:
            context = factory.call_args.kwargs["verify"]
            assert isinstance(context, ssl.SSLContext)
            assert context.verify_mode == ssl.CERT_REQUIRED
            assert context.check_hostname
            system_roots = ssl.create_default_context().get_ca_certs(binary_form=True)
            assert system_roots
            assert set(system_roots) <= set(context.get_ca_certs(binary_form=True))
            assert client.timeout.read == 7.0


async def test_client_loads_ca_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    root = ssl.create_default_context().get_ca_certs(binary_form=True)[0]
    bundle = tmp_path / "ca.pem"
    bundle.write_text(ssl.DER_cert_to_PEM_cert(root), encoding="ascii")
    monkeypatch.setenv("SSL_CERT_FILE", str(bundle))
    with patch("notification.http_client.AsyncClient", wraps=httpx.AsyncClient) as factory:
        async with create_http_client():
            context = factory.call_args.kwargs["verify"]
            assert root in context.get_ca_certs(binary_form=True)
            assert context.verify_mode == ssl.CERT_REQUIRED
            assert context.check_hostname
