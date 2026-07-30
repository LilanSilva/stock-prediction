"""Typed exceptions for the Credibility Service."""

from __future__ import annotations


class CredibilityError(Exception):
    """Base class for credibility-service errors."""


class InvalidScoredMessageError(CredibilityError):
    """Raised when a consumed PredictionScored cannot be applied (e.g. an unparseable edge_id).

    Terminal: the message is dead-lettered rather than retried, since re-delivery cannot fix it.
    """
