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

// --- Company-level events (version 1.1) ---
MERGE (c:CausalFactor {id: 'CORPORATE_ACQUISITION'})   SET c.name = 'Corporate acquisition / merger', c.category = 'corporate';
MERGE (c:CausalFactor {id: 'EXECUTIVE_CHANGE'})        SET c.name = 'Executive change',               c.category = 'corporate';
MERGE (c:CausalFactor {id: 'REGULATORY_ACTION'})       SET c.name = 'Regulatory action',              c.category = 'corporate';
MERGE (c:CausalFactor {id: 'DEBT_CRISIS'})             SET c.name = 'Debt crisis / bankruptcy',       c.category = 'corporate';
MERGE (c:CausalFactor {id: 'RESTRUCTURING'})           SET c.name = 'Restructuring / layoffs',        c.category = 'corporate';
MERGE (c:CausalFactor {id: 'LEGAL_DISPUTE'})           SET c.name = 'Legal dispute / litigation',     c.category = 'corporate';
MERGE (c:CausalFactor {id: 'PRODUCT_RECALL'})          SET c.name = 'Product recall',                 c.category = 'corporate';
MERGE (c:CausalFactor {id: 'DIVIDEND_CHANGE'})         SET c.name = 'Dividend change',                c.category = 'corporate';
MERGE (c:CausalFactor {id: 'CONTRACT_WIN'})            SET c.name = 'Contract win / new deal',        c.category = 'corporate';
MERGE (c:CausalFactor {id: 'SHARE_BUYBACK'})           SET c.name = 'Share buyback',                  c.category = 'corporate';
MERGE (c:CausalFactor {id: 'IPO_LISTING'})             SET c.name = 'IPO / stock market listing',     c.category = 'corporate';
MERGE (c:CausalFactor {id: 'CYBERSECURITY_INCIDENT'})  SET c.name = 'Cybersecurity incident',         c.category = 'corporate';

// --- Macro / country-level events (version 1.1) ---
MERGE (c:CausalFactor {id: 'TRADE_POLICY'})            SET c.name = 'Trade policy / tariffs',         c.category = 'macro';
MERGE (c:CausalFactor {id: 'FISCAL_POLICY'})           SET c.name = 'Fiscal policy / stimulus',       c.category = 'macro';
MERGE (c:CausalFactor {id: 'CURRENCY_CRISIS'})         SET c.name = 'Currency crisis',                c.category = 'macro';
MERGE (c:CausalFactor {id: 'SOVEREIGN_DEBT'})          SET c.name = 'Sovereign debt crisis',          c.category = 'macro';
MERGE (c:CausalFactor {id: 'GEOPOLITICAL_TENSION'})    SET c.name = 'Geopolitical tension',           c.category = 'geopolitical';
MERGE (c:CausalFactor {id: 'COMMODITY_PRICE_SHOCK'})   SET c.name = 'Commodity price shock',          c.category = 'commodity_supply';

// --- Market / financial system events (version 1.1) ---
MERGE (c:CausalFactor {id: 'ECONOMIC_DATA_RELEASE'})   SET c.name = 'Economic data release',          c.category = 'economic_data';
MERGE (c:CausalFactor {id: 'PANDEMIC_OUTBREAK'})       SET c.name = 'Pandemic / epidemic outbreak',   c.category = 'natural';
MERGE (c:CausalFactor {id: 'ENERGY_POLICY'})           SET c.name = 'Energy policy',                  c.category = 'macro';
