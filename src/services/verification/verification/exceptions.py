"""Typed exceptions for the Verification Service."""

from __future__ import annotations


class VerificationError(Exception):
    """Base class for verification-service errors."""


class InvalidPredictionError(VerificationError):
    """Raised when a consumed prediction cannot be evaluated (non-canonical/unknown asset)."""


class PriceValidationError(VerificationError):
    """Raised when a PriceObserved does not match its evaluation's frozen policy/sessions."""
