# Canonical Asset Registry

Business messages use `asset_id`. Adapters translate that ID to a versioned provider reference series. Provider symbols must never be used as cross-service business identifiers.

## POC-6 validation status

The P06 controlled rerun approved a POC-only Yahoo reference-close policy for the 2026-07-13 evaluation:

- `GC=F` is used for `GOLD` as a COMEX/CMX USD futures reference.
- `BZ=F` is used for `BRENT_OIL` as the Yahoo Brent Crude Oil Last Day Financial futures reference with `NYM` metadata.
- Values are raw Yahoo `PROVIDER_DAILY_CLOSE` observations, not official exchange settlements.
- Continuous futures use `PROVIDER_MANAGED_CONTINUOUS_INCLUDE_ALL_V1`.
- Stooq remains unvalidated and is not enabled as fallback.

The policy is sufficient for POC scoring and graph-only M1 scope. It is not an authoritative settlement-source policy for production or expanded assets.

## Initial POC assets

| asset_id | Display | Class | Currency | Intended market | Candidate Yahoo series | Provider metadata observed | Fallback | Status |
|---|---|---|---|---|---|---|---|---|
| `GOLD` | Gold | Commodity | USD | COMEX / America-New_York | `GC=F` | `CMX`, America/New_York | none validated | POC reference-close approved |
| `BRENT_OIL` | Brent crude oil | Commodity | USD | Yahoo Brent Crude Oil Last Day Financial / America-New_York provider calendar | `BZ=F` | `NYM`, America/New_York | none validated | POC reference-close approved |

## Required reference-series fields

Before an asset may be scored, its registry version must declare:

- provider and provider symbol;
- instrument type and currency;
- intended economic asset and provider exchange metadata;
- session-date mapping and timezone;
- `price_kind`, initially either `PROVIDER_DAILY_CLOSE` or `OFFICIAL_SETTLEMENT`;
- whether prices are raw or adjusted;
- continuous-futures rollover policy;
- fallback mapping or explicit `null`;
- validation date and evidence.

Yahoo daily data used by the POC is `PROVIDER_DAILY_CLOSE`. It must not be described as an official exchange settlement.

## Deferred assets

`USD_INDEX`, `OMXS30`, `SP500`, and individual equities are deferred until the walking skeleton succeeds and their provider mappings, currency, calendars, and fallback quality are validated.

## Registry rules

- Registry changes are reviewed as contract changes.
- Canonical market identity and provider bar metadata are recorded separately when they differ.
- A provider timestamp is not assumed to be an official settlement time.
- Fallbacks must represent the same economic instrument, currency, session semantics, and adjustment policy.
- An unavailable fallback is `null`; the system must not silently substitute a different listing, ETF, or contract.
- Rollover exclusions are declared before outcomes are evaluated.
- API filters accept canonical IDs. UI labels come from the registry.
