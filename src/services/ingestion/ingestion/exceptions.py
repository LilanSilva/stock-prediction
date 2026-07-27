"""Typed ingestion exceptions."""

from __future__ import annotations


class IngestionError(Exception):
    """Base class for ingestion errors."""


class AdapterError(IngestionError):
    """A source adapter failed to fetch or parse its feed."""


class BodyFetchError(IngestionError):
    """The body fetcher could not safely retrieve article content."""
