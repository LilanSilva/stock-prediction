"""Text normalization and content hashing for canonical article records."""

from __future__ import annotations

import hashlib
import re
import unicodedata

_WHITESPACE = re.compile(r"\s+")


def normalize_text(value: str) -> str:
    """NFC-normalize Unicode, collapse runs of whitespace, and strip ends."""
    normalized = unicodedata.normalize("NFC", value)
    return _WHITESPACE.sub(" ", normalized).strip()


def truncate(value: str, max_chars: int) -> str:
    """Truncate to at most `max_chars` characters (UTF-8 safe at the character level)."""
    return value if len(value) <= max_chars else value[:max_chars]


def content_hash(title: str, body: str) -> str:
    """Stable SHA-256 over normalized title + body for content-based de-duplication."""
    payload = f"{normalize_text(title)}\x00{normalize_text(body)}".encode()
    return hashlib.sha256(payload).hexdigest()
