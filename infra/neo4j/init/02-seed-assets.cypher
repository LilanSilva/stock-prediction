// Seed canonical assets and industry groups (GENERATED -- do not edit by hand).
//
// Regenerate with:  python scripts/generate-asset-seed.py
// Source of truth:  src/shared/shared/reference/assets.json
//
// Every asset belongs to exactly one (:AssetGroup) via MEMBER_OF. Causal edges may be attached at
// either level: prediction reads an asset's own CAUSES edges when they exist and otherwise inherits
// its group's, so a newly added listing predicts from day one instead of waiting for the offline
// learner to accumulate enough samples for a company-specific edge.
//
// Asset.id is a CANONICAL asset id. Provider symbols (XAUUSD, SAAB-B.ST, ...) live only in the
// registry and in Market Data adapters, never on graph nodes.
//
// MERGE keeps this idempotent so the seed container can re-run safely.


// --- Weapon & defence industry ---
MERGE (g:AssetGroup {id: 'WEAPON_INDUSTRY'})
SET g.name = 'Weapon & defence industry';
MERGE (a:Asset {id: 'LMT_NYSE'})
SET a.name = 'Lockheed Martin', a.asset_class = 'equity', a.currency = 'USD', a.market = 'NYSE', a.timezone = 'America/New_York';
MATCH (a:Asset {id: 'LMT_NYSE'}), (g:AssetGroup {id: 'WEAPON_INDUSTRY'})
MERGE (a)-[:MEMBER_OF]->(g);
MERGE (a:Asset {id: 'SAAB_B_STO'})
SET a.name = 'Saab AB', a.asset_class = 'equity', a.currency = 'SEK', a.market = 'STO', a.timezone = 'Europe/Stockholm';
MATCH (a:Asset {id: 'SAAB_B_STO'}), (g:AssetGroup {id: 'WEAPON_INDUSTRY'})
MERGE (a)-[:MEMBER_OF]->(g);
MERGE (a:Asset {id: 'AM_EPA'})
SET a.name = 'Dassault Aviation', a.asset_class = 'equity', a.currency = 'EUR', a.market = 'PAR', a.timezone = 'Europe/Paris';
MATCH (a:Asset {id: 'AM_EPA'}), (g:AssetGroup {id: 'WEAPON_INDUSTRY'})
MERGE (a)-[:MEMBER_OF]->(g);

// --- Aerospace & aviation ---
MERGE (g:AssetGroup {id: 'AEROSPACE_AVIATION'})
SET g.name = 'Aerospace & aviation';
MERGE (a:Asset {id: 'BA_NYSE'})
SET a.name = 'Boeing', a.asset_class = 'equity', a.currency = 'USD', a.market = 'NYSE', a.timezone = 'America/New_York';
MATCH (a:Asset {id: 'BA_NYSE'}), (g:AssetGroup {id: 'AEROSPACE_AVIATION'})
MERGE (a)-[:MEMBER_OF]->(g);
MERGE (a:Asset {id: 'UAL_NASDAQ'})
SET a.name = 'United Airlines', a.asset_class = 'equity', a.currency = 'USD', a.market = 'NASDAQ', a.timezone = 'America/New_York';
MATCH (a:Asset {id: 'UAL_NASDAQ'}), (g:AssetGroup {id: 'AEROSPACE_AVIATION'})
MERGE (a)-[:MEMBER_OF]->(g);

// --- Pharmaceutical industry ---
MERGE (g:AssetGroup {id: 'PHARMA_INDUSTRY'})
SET g.name = 'Pharmaceutical industry';
MERGE (a:Asset {id: 'AZN_STO'})
SET a.name = 'AstraZeneca', a.asset_class = 'equity', a.currency = 'SEK', a.market = 'STO', a.timezone = 'Europe/Stockholm';
MATCH (a:Asset {id: 'AZN_STO'}), (g:AssetGroup {id: 'PHARMA_INDUSTRY'})
MERGE (a)-[:MEMBER_OF]->(g);
MERGE (a:Asset {id: 'NOVO_B_CPH'})
SET a.name = 'Novo Nordisk', a.asset_class = 'equity', a.currency = 'DKK', a.market = 'CPH', a.timezone = 'Europe/Copenhagen';
MATCH (a:Asset {id: 'NOVO_B_CPH'}), (g:AssetGroup {id: 'PHARMA_INDUSTRY'})
MERGE (a)-[:MEMBER_OF]->(g);
MERGE (a:Asset {id: 'BNTX_NASDAQ'})
SET a.name = 'BioNTech', a.asset_class = 'equity', a.currency = 'USD', a.market = 'NASDAQ', a.timezone = 'America/New_York';
MATCH (a:Asset {id: 'BNTX_NASDAQ'}), (g:AssetGroup {id: 'PHARMA_INDUSTRY'})
MERGE (a)-[:MEMBER_OF]->(g);
MERGE (a:Asset {id: 'MRNA_NASDAQ'})
SET a.name = 'Moderna', a.asset_class = 'equity', a.currency = 'USD', a.market = 'NASDAQ', a.timezone = 'America/New_York';
MATCH (a:Asset {id: 'MRNA_NASDAQ'}), (g:AssetGroup {id: 'PHARMA_INDUSTRY'})
MERGE (a)-[:MEMBER_OF]->(g);

// --- Semiconductor industry ---
MERGE (g:AssetGroup {id: 'SEMICONDUCTOR_INDUSTRY'})
SET g.name = 'Semiconductor industry';
MERGE (a:Asset {id: 'NVDA_NASDAQ'})
SET a.name = 'Nvidia', a.asset_class = 'equity', a.currency = 'USD', a.market = 'NASDAQ', a.timezone = 'America/New_York';
MATCH (a:Asset {id: 'NVDA_NASDAQ'}), (g:AssetGroup {id: 'SEMICONDUCTOR_INDUSTRY'})
MERGE (a)-[:MEMBER_OF]->(g);
MERGE (a:Asset {id: 'ASML_AMS'})
SET a.name = 'ASML Holding', a.asset_class = 'equity', a.currency = 'EUR', a.market = 'AMS', a.timezone = 'Europe/Amsterdam';
MATCH (a:Asset {id: 'ASML_AMS'}), (g:AssetGroup {id: 'SEMICONDUCTOR_INDUSTRY'})
MERGE (a)-[:MEMBER_OF]->(g);

// --- Software & cloud ---
MERGE (g:AssetGroup {id: 'SOFTWARE_INDUSTRY'})
SET g.name = 'Software & cloud';
MERGE (a:Asset {id: 'MSFT_NASDAQ'})
SET a.name = 'Microsoft', a.asset_class = 'equity', a.currency = 'USD', a.market = 'NASDAQ', a.timezone = 'America/New_York';
MATCH (a:Asset {id: 'MSFT_NASDAQ'}), (g:AssetGroup {id: 'SOFTWARE_INDUSTRY'})
MERGE (a)-[:MEMBER_OF]->(g);
MERGE (a:Asset {id: 'SAP_ETR'})
SET a.name = 'SAP SE', a.asset_class = 'equity', a.currency = 'EUR', a.market = 'ETR', a.timezone = 'Europe/Berlin';
MATCH (a:Asset {id: 'SAP_ETR'}), (g:AssetGroup {id: 'SOFTWARE_INDUSTRY'})
MERGE (a)-[:MEMBER_OF]->(g);
MERGE (a:Asset {id: 'ZM_NASDAQ'})
SET a.name = 'Zoom Communications', a.asset_class = 'equity', a.currency = 'USD', a.market = 'NASDAQ', a.timezone = 'America/New_York';
MATCH (a:Asset {id: 'ZM_NASDAQ'}), (g:AssetGroup {id: 'SOFTWARE_INDUSTRY'})
MERGE (a)-[:MEMBER_OF]->(g);

// --- Internet platforms & e-commerce ---
MERGE (g:AssetGroup {id: 'INTERNET_PLATFORMS'})
SET g.name = 'Internet platforms & e-commerce';
MERGE (a:Asset {id: 'GOOGL_NASDAQ'})
SET a.name = 'Alphabet', a.asset_class = 'equity', a.currency = 'USD', a.market = 'NASDAQ', a.timezone = 'America/New_York';
MATCH (a:Asset {id: 'GOOGL_NASDAQ'}), (g:AssetGroup {id: 'INTERNET_PLATFORMS'})
MERGE (a)-[:MEMBER_OF]->(g);
MERGE (a:Asset {id: 'AMZN_NASDAQ'})
SET a.name = 'Amazon', a.asset_class = 'equity', a.currency = 'USD', a.market = 'NASDAQ', a.timezone = 'America/New_York';
MATCH (a:Asset {id: 'AMZN_NASDAQ'}), (g:AssetGroup {id: 'INTERNET_PLATFORMS'})
MERGE (a)-[:MEMBER_OF]->(g);
MERGE (a:Asset {id: 'NFLX_NASDAQ'})
SET a.name = 'Netflix', a.asset_class = 'equity', a.currency = 'USD', a.market = 'NASDAQ', a.timezone = 'America/New_York';
MATCH (a:Asset {id: 'NFLX_NASDAQ'}), (g:AssetGroup {id: 'INTERNET_PLATFORMS'})
MERGE (a)-[:MEMBER_OF]->(g);

// --- Consumer electronics ---
MERGE (g:AssetGroup {id: 'CONSUMER_ELECTRONICS'})
SET g.name = 'Consumer electronics';
MERGE (a:Asset {id: 'AAPL_NASDAQ'})
SET a.name = 'Apple', a.asset_class = 'equity', a.currency = 'USD', a.market = 'NASDAQ', a.timezone = 'America/New_York';
MATCH (a:Asset {id: 'AAPL_NASDAQ'}), (g:AssetGroup {id: 'CONSUMER_ELECTRONICS'})
MERGE (a)-[:MEMBER_OF]->(g);

// --- Automotive industry ---
MERGE (g:AssetGroup {id: 'AUTOMOTIVE_INDUSTRY'})
SET g.name = 'Automotive industry';
MERGE (a:Asset {id: 'TSLA_NASDAQ'})
SET a.name = 'Tesla', a.asset_class = 'equity', a.currency = 'USD', a.market = 'NASDAQ', a.timezone = 'America/New_York';
MATCH (a:Asset {id: 'TSLA_NASDAQ'}), (g:AssetGroup {id: 'AUTOMOTIVE_INDUSTRY'})
MERGE (a)-[:MEMBER_OF]->(g);

// --- Oil, gas & refining ---
MERGE (g:AssetGroup {id: 'OIL_GAS'})
SET g.name = 'Oil, gas & refining';
MERGE (a:Asset {id: 'BRENT_OIL'})
SET a.name = 'Brent crude oil', a.asset_class = 'commodity', a.currency = 'USD', a.market = 'NYMEX', a.timezone = 'America/New_York';
MATCH (a:Asset {id: 'BRENT_OIL'}), (g:AssetGroup {id: 'OIL_GAS'})
MERGE (a)-[:MEMBER_OF]->(g);
MERGE (a:Asset {id: 'XOM_NYSE'})
SET a.name = 'Exxon Mobil', a.asset_class = 'equity', a.currency = 'USD', a.market = 'NYSE', a.timezone = 'America/New_York';
MATCH (a:Asset {id: 'XOM_NYSE'}), (g:AssetGroup {id: 'OIL_GAS'})
MERGE (a)-[:MEMBER_OF]->(g);
MERGE (a:Asset {id: 'FANG_NASDAQ'})
SET a.name = 'Diamondback Energy', a.asset_class = 'equity', a.currency = 'USD', a.market = 'NASDAQ', a.timezone = 'America/New_York';
MATCH (a:Asset {id: 'FANG_NASDAQ'}), (g:AssetGroup {id: 'OIL_GAS'})
MERGE (a)-[:MEMBER_OF]->(g);
MERGE (a:Asset {id: 'VLO_NYSE'})
SET a.name = 'Valero Energy', a.asset_class = 'equity', a.currency = 'USD', a.market = 'NYSE', a.timezone = 'America/New_York';
MATCH (a:Asset {id: 'VLO_NYSE'}), (g:AssetGroup {id: 'OIL_GAS'})
MERGE (a)-[:MEMBER_OF]->(g);

// --- Precious metals & mining ---
MERGE (g:AssetGroup {id: 'PRECIOUS_METALS'})
SET g.name = 'Precious metals & mining';
MERGE (a:Asset {id: 'GOLD'})
SET a.name = 'Gold', a.asset_class = 'commodity', a.currency = 'USD', a.market = 'COMEX', a.timezone = 'America/New_York';
MATCH (a:Asset {id: 'GOLD'}), (g:AssetGroup {id: 'PRECIOUS_METALS'})
MERGE (a)-[:MEMBER_OF]->(g);
MERGE (a:Asset {id: 'LUG_STO'})
SET a.name = 'Lundin Gold', a.asset_class = 'equity', a.currency = 'SEK', a.market = 'STO', a.timezone = 'Europe/Stockholm';
MATCH (a:Asset {id: 'LUG_STO'}), (g:AssetGroup {id: 'PRECIOUS_METALS'})
MERGE (a)-[:MEMBER_OF]->(g);

// --- Banking & investment ---
MERGE (g:AssetGroup {id: 'BANKING_FINANCE'})
SET g.name = 'Banking & investment';
MERGE (a:Asset {id: 'SWED_A_STO'})
SET a.name = 'Swedbank', a.asset_class = 'equity', a.currency = 'SEK', a.market = 'STO', a.timezone = 'Europe/Stockholm';
MATCH (a:Asset {id: 'SWED_A_STO'}), (g:AssetGroup {id: 'BANKING_FINANCE'})
MERGE (a)-[:MEMBER_OF]->(g);
MERGE (a:Asset {id: 'INVE_B_STO'})
SET a.name = 'Investor AB', a.asset_class = 'equity', a.currency = 'SEK', a.market = 'STO', a.timezone = 'Europe/Stockholm';
MATCH (a:Asset {id: 'INVE_B_STO'}), (g:AssetGroup {id: 'BANKING_FINANCE'})
MERGE (a)-[:MEMBER_OF]->(g);

// --- Industrial manufacturing ---
MERGE (g:AssetGroup {id: 'INDUSTRIAL_MANUFACTURING'})
SET g.name = 'Industrial manufacturing';
MERGE (a:Asset {id: 'ATCO_A_STO'})
SET a.name = 'Atlas Copco', a.asset_class = 'equity', a.currency = 'SEK', a.market = 'STO', a.timezone = 'Europe/Stockholm';
MATCH (a:Asset {id: 'ATCO_A_STO'}), (g:AssetGroup {id: 'INDUSTRIAL_MANUFACTURING'})
MERGE (a)-[:MEMBER_OF]->(g);
MERGE (a:Asset {id: 'ABB_STO'})
SET a.name = 'ABB Ltd', a.asset_class = 'equity', a.currency = 'SEK', a.market = 'STO', a.timezone = 'Europe/Stockholm';
MATCH (a:Asset {id: 'ABB_STO'}), (g:AssetGroup {id: 'INDUSTRIAL_MANUFACTURING'})
MERGE (a)-[:MEMBER_OF]->(g);
MERGE (a:Asset {id: 'ADDT_B_STO'})
SET a.name = 'Addtech', a.asset_class = 'equity', a.currency = 'SEK', a.market = 'STO', a.timezone = 'Europe/Stockholm';
MATCH (a:Asset {id: 'ADDT_B_STO'}), (g:AssetGroup {id: 'INDUSTRIAL_MANUFACTURING'})
MERGE (a)-[:MEMBER_OF]->(g);

// --- Telecom & network equipment ---
MERGE (g:AssetGroup {id: 'TELECOM'})
SET g.name = 'Telecom & network equipment';
MERGE (a:Asset {id: 'TELIA_STO'})
SET a.name = 'Telia Company', a.asset_class = 'equity', a.currency = 'SEK', a.market = 'STO', a.timezone = 'Europe/Stockholm';
MATCH (a:Asset {id: 'TELIA_STO'}), (g:AssetGroup {id: 'TELECOM'})
MERGE (a)-[:MEMBER_OF]->(g);

// --- Food & ingredients ---
MERGE (g:AssetGroup {id: 'FOOD_INGREDIENTS'})
SET g.name = 'Food & ingredients';
MERGE (a:Asset {id: 'AAK_STO'})
SET a.name = 'AAK AB', a.asset_class = 'equity', a.currency = 'SEK', a.market = 'STO', a.timezone = 'Europe/Stockholm';
MATCH (a:Asset {id: 'AAK_STO'}), (g:AssetGroup {id: 'FOOD_INGREDIENTS'})
MERGE (a)-[:MEMBER_OF]->(g);

// --- Real estate ---
MERGE (g:AssetGroup {id: 'REAL_ESTATE'})
SET g.name = 'Real estate';
MERGE (a:Asset {id: 'SPG_NYSE'})
SET a.name = 'Simon Property Group', a.asset_class = 'equity', a.currency = 'USD', a.market = 'NYSE', a.timezone = 'America/New_York';
MATCH (a:Asset {id: 'SPG_NYSE'}), (g:AssetGroup {id: 'REAL_ESTATE'})
MERGE (a)-[:MEMBER_OF]->(g);

// --- Consumer fitness & leisure ---
MERGE (g:AssetGroup {id: 'CONSUMER_FITNESS'})
SET g.name = 'Consumer fitness & leisure';
MERGE (a:Asset {id: 'PTON_NASDAQ'})
SET a.name = 'Peloton Interactive', a.asset_class = 'equity', a.currency = 'USD', a.market = 'NASDAQ', a.timezone = 'America/New_York';
MATCH (a:Asset {id: 'PTON_NASDAQ'}), (g:AssetGroup {id: 'CONSUMER_FITNESS'})
MERGE (a)-[:MEMBER_OF]->(g);
