"""Feed Analyzer Market Data Service.

Consumes canonical `PriceRequested` messages (sole producer: Verification), fetches the approved
provider reference closes for the requested baseline and settlement sessions, persists them
immutably, and publishes exactly one `PriceObserved` per request via a transactional outbox.

The service never evaluates prediction correctness and never labels provider daily closes as
official exchange settlements.
"""

from __future__ import annotations

__version__ = "0.1.0"
