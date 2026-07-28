"""Typed exceptions for the Market Data Service.

The retry/pending/terminal distinction drives the request lifecycle:
  - PriceNotYetAvailableError -> request stays PENDING with a bounded next attempt (not a failure).
  - AdapterUnavailableError   -> transient; retried with bounded backoff.
  - InvalidObservationError   -> terminal; the request is quarantined and the message dead-lettered.
"""

from __future__ import annotations


class MarketDataError(Exception):
    """Base class for market-data service errors."""


class PriceNotYetAvailableError(MarketDataError):
    """The requested session close is not yet published by the provider.

    Not an error condition: the request remains pending and is retried after the session completes.
    """


class AdapterUnavailableError(MarketDataError):
    """The provider could not be reached or returned a transient failure after retries."""


class InvalidObservationError(MarketDataError):
    """Provider data failed a terminal validation (instrument, currency, session, or close).

    Terminal: the request is quarantined and the input message is dead-lettered rather than retried,
    because retrying identical bad data cannot succeed.
    """
