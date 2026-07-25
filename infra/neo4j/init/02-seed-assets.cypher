// Seed canonical POC assets (docs/reference/asset-registry.md).
//
// Only GOLD and BRENT_OIL are in scope for the M1 walking skeleton. USD_INDEX, OMXS30, SP500 and
// individual equities are DEFERRED by the registry until the skeleton succeeds and their provider
// mappings are validated — they are intentionally not seeded here. The legacy "10 assets" backlog
// note (with provider tickers like GC=F on nodes) is overridden: provider symbols live only in
// Market Data adapters, never on graph nodes.
//
// MERGE keeps this idempotent so the seed container can re-run safely.

MERGE (a:Asset {id: 'GOLD'})
SET a.name = 'Gold',
    a.asset_class = 'commodity',
    a.currency = 'USD';

MERGE (a:Asset {id: 'BRENT_OIL'})
SET a.name = 'Brent crude oil',
    a.asset_class = 'commodity',
    a.currency = 'USD';
