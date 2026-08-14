"""Typed exceptions for the Verification Service."""

from __future__ import annotations


class VerificationError(Exception):
    """Base class for verification-service errors."""


class InvalidPredictionError(VerificationError):
    """Raised when a consumed prediction cannot be evaluated (non-canonical/unknown asset)."""


class PriceValidationError(VerificationError):
    """Raised when a PriceObserved does not match its evaluation's frozen policy/sessions."""


class OrphanedObservationError(VerificationError):
    """Raised when a PriceObserved names a request that has no evaluation row.

    Deliberately distinct from :class:`PriceValidationError`. That one means the observation and its
    evaluation disagree — a contract violation worth preserving in the dead-letter queue for a human
    to inspect. This one means there is nothing to compare against at all, which no retry and no
    inspection of the message itself can resolve: the evidence needed is the missing row, not the
    message.

    The two were conflated, so every orphan was dead-lettered as poison. The dead-letter queue then
    filled with unactionable messages — 14 of them, all orphans, none a real defect — which is the
    noise that makes an operator stop trusting a DLQ.
    """
