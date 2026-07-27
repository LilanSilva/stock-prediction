"""Typed exceptions for the Cleansing Service."""

from __future__ import annotations


class CleansingError(Exception):
    """Base class for all cleansing-domain errors."""


class EmbeddingUnavailableError(CleansingError):
    """The embedding backend is not ready (readiness must fail, no new work consumed)."""


class ExtractionError(CleansingError):
    """Local NLP extraction failed for an article."""


class AmbiguousMergeError(CleansingError):
    """A cluster is ambiguous and requires LLM assistance that is unavailable."""
