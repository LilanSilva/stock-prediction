"""URL canonicalization for de-duplication.

Two source URLs that point at the same article (differing only by tracking parameters, fragments,
default ports, or case in the host) must canonicalize to the same string so the storage layer treats
them as one article.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Query parameters that never identify content; dropped during canonicalization.
_TRACKING_PREFIXES = ("utm_",)
_TRACKING_KEYS = frozenset(
    {"fbclid", "gclid", "igshid", "mc_cid", "mc_eid", "ref", "ref_src", "cmpid"}
)
_DEFAULT_PORTS = {"http": "80", "https": "443"}


def _is_tracking_param(key: str) -> bool:
    lowered = key.lower()
    return lowered in _TRACKING_KEYS or any(lowered.startswith(p) for p in _TRACKING_PREFIXES)


def canonicalize_url(url: str) -> str:
    """Return a stable canonical form of `url` for equality/dedup.

    Lowercases scheme and host, removes the default port, drops tracking query parameters and the
    fragment, sorts the remaining query, and trims a trailing slash on non-root paths.
    """
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = parts.hostname or ""

    netloc = host
    if parts.port is not None and str(parts.port) != _DEFAULT_PORTS.get(scheme):
        netloc = f"{host}:{parts.port}"

    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if not _is_tracking_param(k)
    ]
    query = urlencode(sorted(kept))

    path = parts.path
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    return urlunsplit((scheme, netloc, path, query, ""))
