# T03: Reference Price, Calendar, and Rollover Policy

**Status:** COMPLETE

**Evidence:** [market_policy.py](../../../../src/poc/poc6/market_policy.py), [reference price policy](../../../../src/poc/poc6/results/reference-price-policy.json), and [market observations](../../../../src/poc/poc6/data/frozen/market-observations.json). P06 uses Yahoo raw provider daily closes for `GC=F` and `BZ=F`, with no validated fallback.

## Purpose

Define the exact provider observations used as POC ground truth before predictions are evaluated.

## Requirements

- Validate `GC=F` and `BZ=F` instrument identity, currency, provider exchange/timezone, session label, and daily close semantics.
- Resolve the `BZ=F` Yahoo metadata versus ICE Europe registry mismatch.
- Call Yahoo values `provider-reported reference closes`; do not claim official settlement status.
- Define `provider_bar_time`, `fetched_at`, `session`, `price_kind`, and source fields separately.
- Pre-declare a continuous-futures rollover inclusion/exclusion policy without looking at prediction correctness.
- Validate a fallback with equivalent instrument/currency/session semantics, or explicitly declare no fallback for the POC.
- Use raw daily close values; do not silently auto-adjust or substitute an ETF/other contract.

## Acceptance criteria

1. Both assets have an approved, versioned reference-series policy or are removed from the rerun scope.
2. Known holiday and rollover examples map deterministically to baseline and settlement sessions.
3. Stooq is not enabled unless live CSV and semantic-equivalence tests pass.
4. The same stored observations reproduce every score.
5. No timestamp is described as an official settlement observation unless the provider supplies that guarantee.
