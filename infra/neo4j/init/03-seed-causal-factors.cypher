// Seed causal factors using canonical EventType taxonomy values (docs/reference/event-taxonomy.md).
//
// CausalFactor.id MUST equal a taxonomy EventType so Cleansing output and Prediction matching share
// one vocabulary. OTHER is a catch-all event type and is intentionally not seeded as a causal factor.
//
// MERGE keeps this idempotent.

MERGE (c:CausalFactor {id: 'MILITARY_CONFLICT'})   SET c.name = 'Military conflict',        c.category = 'geopolitical';
MERGE (c:CausalFactor {id: 'STRAIT_CLOSURE'})      SET c.name = 'Strait / passage closure', c.category = 'geopolitical';
MERGE (c:CausalFactor {id: 'SUPPLY_DISRUPTION'})   SET c.name = 'Supply disruption',        c.category = 'commodity_supply';
MERGE (c:CausalFactor {id: 'SANCTIONS'})           SET c.name = 'Sanctions',                c.category = 'geopolitical';
MERGE (c:CausalFactor {id: 'RATE_DECISION'})       SET c.name = 'Central bank rate decision', c.category = 'monetary_policy';
MERGE (c:CausalFactor {id: 'INFLATION_CHANGE'})    SET c.name = 'Inflation change',         c.category = 'economic_data';
MERGE (c:CausalFactor {id: 'RECESSION_SIGNAL'})    SET c.name = 'Recession signal',         c.category = 'macro_sentiment';
MERGE (c:CausalFactor {id: 'CORPORATE_EARNINGS'})  SET c.name = 'Corporate earnings',       c.category = 'corporate';
MERGE (c:CausalFactor {id: 'POLITICAL_TRANSITION'}) SET c.name = 'Political transition',    c.category = 'geopolitical';
MERGE (c:CausalFactor {id: 'NATURAL_DISASTER'})    SET c.name = 'Natural disaster',         c.category = 'natural';
