"""Text normalization and content hashing for canonical article records."""

from __future__ import annotations

import hashlib
import re
import unicodedata

_WHITESPACE = re.compile(r"\s+")
# Matches the start of a full HTML page dump (case-insensitive, optional BOM).
_HTML_PAGE = re.compile(r"^\s*(?:﻿)?(?:<!doctype\s+html|<html[\s>])", re.IGNORECASE)


def normalize_text(value: str) -> str:
    """NFC-normalize Unicode, collapse runs of whitespace, and strip ends."""
    normalized = unicodedata.normalize("NFC", value)
    return _WHITESPACE.sub(" ", normalized).strip()


def truncate(value: str, max_chars: int) -> str:
    """Truncate to at most `max_chars` characters (UTF-8 safe at the character level)."""
    return value if len(value) <= max_chars else value[:max_chars]


def strip_html_page(body: str) -> str:
    """Return "" when body is a full HTML page dump, otherwise return body unchanged.

    Sources like svd.se and bbc.com return the entire page HTML instead of article text.
    That content is all <head>, <script>, and <meta> — no readable news. Storing it
    contaminates SimHash fingerprints and embeddings in Cleansing. A full page always
    starts with <!DOCTYPE html> or <html, so a single pattern check on the first 200
    characters identifies the case cheaply. Plain text and RSS summaries pass through.
    """
    if _HTML_PAGE.match(body[:200]):
        return ""
    return body


def content_hash(title: str, body: str) -> str:
    """Stable SHA-256 over normalized title + body for content-based de-duplication."""
    payload = f"{normalize_text(title)}\x00{normalize_text(body)}".encode()
    return hashlib.sha256(payload).hexdigest()
