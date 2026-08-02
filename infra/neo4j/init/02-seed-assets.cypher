// Seed canonical assets (docs/reference/asset-registry.md).
//
// GOLD and BRENT_OIL are the original commodities. The sector equities below are each priced by a
// representative large-cap bellwether that biquote.io serves (see shared.reference.asset_registry);
// provider symbols live only in Market Data adapters, never on graph nodes.
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

// --- Industry-sector equities (bellwether-priced) ---
MERGE (a:Asset {id: 'PHARMA'})            SET a.name = 'Pharmaceutical sector',       a.asset_class = 'equity_sector', a.currency = 'USD';
MERGE (a:Asset {id: 'DEFENSE_AEROSPACE'}) SET a.name = 'Defense & aerospace sector',  a.asset_class = 'equity_sector', a.currency = 'USD';
MERGE (a:Asset {id: 'AI_COMPUTE'})        SET a.name = 'AI compute sector',           a.asset_class = 'equity_sector', a.currency = 'USD';
MERGE (a:Asset {id: 'SEMICONDUCTOR'})     SET a.name = 'Semiconductor sector',        a.asset_class = 'equity_sector', a.currency = 'USD';
MERGE (a:Asset {id: 'SOFTWARE'})          SET a.name = 'Software sector',             a.asset_class = 'equity_sector', a.currency = 'USD';
MERGE (a:Asset {id: 'ENTERPRISE_SOFTWARE'}) SET a.name = 'Enterprise software sector', a.asset_class = 'equity_sector', a.currency = 'USD';
MERGE (a:Asset {id: 'INTERNET_SEARCH'})   SET a.name = 'Internet search sector',      a.asset_class = 'equity_sector', a.currency = 'USD';
MERGE (a:Asset {id: 'CONSUMER_ELECTRONICS'}) SET a.name = 'Consumer electronics sector', a.asset_class = 'equity_sector', a.currency = 'USD';
MERGE (a:Asset {id: 'BANKING'})           SET a.name = 'Banking sector',              a.asset_class = 'equity_sector', a.currency = 'USD';
MERGE (a:Asset {id: 'PAYMENTS_FINANCE'})  SET a.name = 'Payments & finance sector',   a.asset_class = 'equity_sector', a.currency = 'USD';
MERGE (a:Asset {id: 'AUTOMOTIVE'})        SET a.name = 'Automotive sector',           a.asset_class = 'equity_sector', a.currency = 'USD';
MERGE (a:Asset {id: 'FOOD_BEVERAGE'})     SET a.name = 'Food & beverage sector',      a.asset_class = 'equity_sector', a.currency = 'USD';
MERGE (a:Asset {id: 'REAL_ESTATE'})       SET a.name = 'Real estate sector',          a.asset_class = 'equity_sector', a.currency = 'USD';
MERGE (a:Asset {id: 'INDUSTRIAL'})        SET a.name = 'Industrial manufacturing sector', a.asset_class = 'equity_sector', a.currency = 'USD';
MERGE (a:Asset {id: 'APPAREL'})           SET a.name = 'Apparel sector',              a.asset_class = 'equity_sector', a.currency = 'USD';
