// Live knowledge graph snapshot (GENERATED -- do not edit by hand).
//
// Regenerate with:  powershell -File scripts\export-kg-snapshot.ps1
// Apply with:       powershell -File scripts\import-kg-snapshot.ps1
// snapshot-format:  v1
// exported-at:      2026-08-30T11:33:22Z
//
// WHAT THIS IS
// infra/neo4j/init/*.cypher seeds expert PRIORS at alpha=1.0, beta=1.0. This file is the
// graph's LEARNED state: weights refined online by Credibility and edges added by the
// offline structure learner, neither of which exists in the seed files. Re-seeding a fresh
// volume without applying this file silently reverts every learned weight to its prior.
//
// Contents at export time:
//   CausalFactor nodes   : 31
//   CAUSES edges         : 248  (11 with evidence beyond the 1.0/1.0 prior)
//   CORRELATES_WITH edges: 5
//
// NOT IN THIS FILE: Asset, AssetGroup and MEMBER_OF. Those come from
// src/shared/shared/reference/assets.json via scripts/generate-asset-seed.py, which stays
// the single source of truth for the registry. Applying this file to a graph whose assets
// are missing leaves those edges uncreated; import-kg-snapshot.ps1 reports that rather
// than letting Cypher drop them silently.
//
// WHY EDGES MATCH ON `condition`, INCLUDING WHEN IT IS NULL
// `MERGE (s)-[r:CAUSES]->(t)` with no properties matches ANY CAUSES edge between the two
// nodes -- including a conditioned one, whose weight it would then overwrite while leaving
// `condition` in place (verified 2026-08-14; see 06-seed-group-edges.cypher's header). The
// OPTIONAL MATCH + FOREACH below is used instead of MERGE precisely because it can express
// "the edge whose condition IS NULL", which MERGE cannot.
//
// Rows are sorted and one per line so this file diffs cleanly. It is overwritten in full
// on every export.

// ==========================================================================
// section: CAUSAL_FACTORS
// ==========================================================================
UNWIND [
  {id: 'COMMODITY_PRICE_SHOCK', name: 'Commodity price shock', category: 'commodity_supply'},
  {id: 'CONTRACT_WIN', name: 'Contract win / new deal', category: 'corporate'},
  {id: 'CORPORATE_ACQUISITION', name: 'Corporate acquisition / merger', category: 'corporate'},
  {id: 'CORPORATE_EARNINGS', name: 'Corporate earnings', category: 'corporate'},
  {id: 'CURRENCY_CRISIS', name: 'Currency crisis', category: 'macro'},
  {id: 'CYBERSECURITY_INCIDENT', name: 'Cybersecurity incident', category: 'corporate'},
  {id: 'DEBT_CRISIS', name: 'Debt crisis / bankruptcy', category: 'corporate'},
  {id: 'DIVIDEND_CHANGE', name: 'Dividend change', category: 'corporate'},
  {id: 'ECONOMIC_DATA_RELEASE', name: 'Economic data release', category: 'economic_data'},
  {id: 'ENERGY_POLICY', name: 'Energy policy', category: 'macro'},
  {id: 'EXECUTIVE_CHANGE', name: 'Executive change', category: 'corporate'},
  {id: 'FISCAL_POLICY', name: 'Fiscal policy / stimulus', category: 'macro'},
  {id: 'GEOPOLITICAL_TENSION', name: 'Geopolitical tension', category: 'geopolitical'},
  {id: 'INFLATION_CHANGE', name: 'Inflation change', category: 'economic_data'},
  {id: 'IPO_LISTING', name: 'IPO / stock market listing', category: 'corporate'},
  {id: 'LEGAL_DISPUTE', name: 'Legal dispute / litigation', category: 'corporate'},
  {id: 'MILITARY_CONFLICT', name: 'Military conflict', category: 'geopolitical'},
  {id: 'NATURAL_DISASTER', name: 'Natural disaster', category: 'natural'},
  {id: 'PANDEMIC_OUTBREAK', name: 'Pandemic / epidemic outbreak', category: 'natural'},
  {id: 'POLITICAL_TRANSITION', name: 'Political transition', category: 'geopolitical'},
  {id: 'PRODUCT_RECALL', name: 'Product recall', category: 'corporate'},
  {id: 'RATE_DECISION', name: 'Central bank rate decision', category: 'monetary_policy'},
  {id: 'RECESSION_SIGNAL', name: 'Recession signal', category: 'macro_sentiment'},
  {id: 'REGULATORY_ACTION', name: 'Regulatory action', category: 'corporate'},
  {id: 'RESTRUCTURING', name: 'Restructuring / layoffs', category: 'corporate'},
  {id: 'SANCTIONS', name: 'Sanctions', category: 'geopolitical'},
  {id: 'SHARE_BUYBACK', name: 'Share buyback', category: 'corporate'},
  {id: 'SOVEREIGN_DEBT', name: 'Sovereign debt crisis', category: 'macro'},
  {id: 'STRAIT_CLOSURE', name: 'Strait / passage closure', category: 'geopolitical'},
  {id: 'SUPPLY_DISRUPTION', name: 'Supply disruption', category: 'commodity_supply'},
  {id: 'TRADE_POLICY', name: 'Trade policy / tariffs', category: 'macro'}
] AS row
MERGE (cf:CausalFactor {id: row.id})
SET cf.name = row.name, cf.category = row.category;

// ==========================================================================
// section: CAUSES
// ==========================================================================
UNWIND [
  {source: 'COMMODITY_PRICE_SHOCK', sourceLabel: 'CausalFactor', target: 'XOM_NYSE', targetLabel: 'Asset', condition: 'TRANSPORT_AFFECTED', direction: 'UP', weight: 0.14104017126306567, confidence: 1.0, alpha: 3.0, beta: 1.0, last_updated: '2026-08-14T17:24:46.795Z'},
  {source: 'COMMODITY_PRICE_SHOCK', sourceLabel: 'CausalFactor', target: 'AEROSPACE_AVIATION', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.4, confidence: 0.55, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.545Z'},
  {source: 'COMMODITY_PRICE_SHOCK', sourceLabel: 'CausalFactor', target: 'AUTOMOTIVE_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.4, confidence: 0.55, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.518Z'},
  {source: 'COMMODITY_PRICE_SHOCK', sourceLabel: 'CausalFactor', target: 'FOOD_INGREDIENTS', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.45, confidence: 0.55, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.532Z'},
  {source: 'COMMODITY_PRICE_SHOCK', sourceLabel: 'CausalFactor', target: 'OIL_GAS', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.5975670481535371, confidence: 0.65, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T21:05:35.851Z'},
  {source: 'COMMODITY_PRICE_SHOCK', sourceLabel: 'CausalFactor', target: 'PRECIOUS_METALS', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.45, confidence: 0.55, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.498Z'},
  {source: 'CONTRACT_WIN', sourceLabel: 'CausalFactor', target: 'AEROSPACE_AVIATION', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.008Z'},
  {source: 'CONTRACT_WIN', sourceLabel: 'CausalFactor', target: 'AUTOMOTIVE_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.008Z'},
  {source: 'CONTRACT_WIN', sourceLabel: 'CausalFactor', target: 'BANKING_FINANCE', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.008Z'},
  {source: 'CONTRACT_WIN', sourceLabel: 'CausalFactor', target: 'CONSUMER_ELECTRONICS', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.008Z'},
  {source: 'CONTRACT_WIN', sourceLabel: 'CausalFactor', target: 'FOOD_INGREDIENTS', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.008Z'},
  {source: 'CONTRACT_WIN', sourceLabel: 'CausalFactor', target: 'INDUSTRIAL_MANUFACTURING', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.008Z'},
  {source: 'CONTRACT_WIN', sourceLabel: 'CausalFactor', target: 'INTERNET_PLATFORMS', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.008Z'},
  {source: 'CONTRACT_WIN', sourceLabel: 'CausalFactor', target: 'OIL_GAS', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.008Z'},
  {source: 'CONTRACT_WIN', sourceLabel: 'CausalFactor', target: 'PHARMA_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.008Z'},
  {source: 'CONTRACT_WIN', sourceLabel: 'CausalFactor', target: 'PRECIOUS_METALS', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.008Z'},
  {source: 'CONTRACT_WIN', sourceLabel: 'CausalFactor', target: 'SEMICONDUCTOR_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.008Z'},
  {source: 'CONTRACT_WIN', sourceLabel: 'CausalFactor', target: 'SOFTWARE_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.008Z'},
  {source: 'CONTRACT_WIN', sourceLabel: 'CausalFactor', target: 'TELECOM', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.008Z'},
  {source: 'CONTRACT_WIN', sourceLabel: 'CausalFactor', target: 'WEAPON_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.008Z'},
  {source: 'CORPORATE_ACQUISITION', sourceLabel: 'CausalFactor', target: 'AEROSPACE_AVIATION', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.4, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.781Z'},
  {source: 'CORPORATE_ACQUISITION', sourceLabel: 'CausalFactor', target: 'AUTOMOTIVE_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.4, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.781Z'},
  {source: 'CORPORATE_ACQUISITION', sourceLabel: 'CausalFactor', target: 'BANKING_FINANCE', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.4, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.781Z'},
  {source: 'CORPORATE_ACQUISITION', sourceLabel: 'CausalFactor', target: 'CONSUMER_ELECTRONICS', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.4, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.781Z'},
  {source: 'CORPORATE_ACQUISITION', sourceLabel: 'CausalFactor', target: 'FOOD_INGREDIENTS', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.4, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.781Z'},
  {source: 'CORPORATE_ACQUISITION', sourceLabel: 'CausalFactor', target: 'INDUSTRIAL_MANUFACTURING', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.4, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.781Z'},
  {source: 'CORPORATE_ACQUISITION', sourceLabel: 'CausalFactor', target: 'INTERNET_PLATFORMS', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.4, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.781Z'},
  {source: 'CORPORATE_ACQUISITION', sourceLabel: 'CausalFactor', target: 'OIL_GAS', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.4, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.781Z'},
  {source: 'CORPORATE_ACQUISITION', sourceLabel: 'CausalFactor', target: 'PHARMA_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.4, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.781Z'},
  {source: 'CORPORATE_ACQUISITION', sourceLabel: 'CausalFactor', target: 'PRECIOUS_METALS', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.4, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.781Z'},
  {source: 'CORPORATE_ACQUISITION', sourceLabel: 'CausalFactor', target: 'SEMICONDUCTOR_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.4, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.781Z'},
  {source: 'CORPORATE_ACQUISITION', sourceLabel: 'CausalFactor', target: 'SOFTWARE_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.4, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.781Z'},
  {source: 'CORPORATE_ACQUISITION', sourceLabel: 'CausalFactor', target: 'TELECOM', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.4, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.781Z'},
  {source: 'CORPORATE_ACQUISITION', sourceLabel: 'CausalFactor', target: 'WEAPON_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.4, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.781Z'},
  {source: 'CORPORATE_EARNINGS', sourceLabel: 'CausalFactor', target: 'AEROSPACE_AVIATION', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.35, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:04.94Z'},
  {source: 'CORPORATE_EARNINGS', sourceLabel: 'CausalFactor', target: 'AUTOMOTIVE_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.35, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:04.94Z'},
  {source: 'CORPORATE_EARNINGS', sourceLabel: 'CausalFactor', target: 'BANKING_FINANCE', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.35, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:04.94Z'},
  {source: 'CORPORATE_EARNINGS', sourceLabel: 'CausalFactor', target: 'CONSUMER_ELECTRONICS', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.35, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:04.94Z'},
  {source: 'CORPORATE_EARNINGS', sourceLabel: 'CausalFactor', target: 'FOOD_INGREDIENTS', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.35, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:04.94Z'},
  {source: 'CORPORATE_EARNINGS', sourceLabel: 'CausalFactor', target: 'INDUSTRIAL_MANUFACTURING', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.35, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:04.94Z'},
  {source: 'CORPORATE_EARNINGS', sourceLabel: 'CausalFactor', target: 'INTERNET_PLATFORMS', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.35, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:04.94Z'},
  {source: 'CORPORATE_EARNINGS', sourceLabel: 'CausalFactor', target: 'OIL_GAS', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.35, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:04.94Z'},
  {source: 'CORPORATE_EARNINGS', sourceLabel: 'CausalFactor', target: 'PHARMA_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.35, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:04.94Z'},
  {source: 'CORPORATE_EARNINGS', sourceLabel: 'CausalFactor', target: 'PRECIOUS_METALS', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.35, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:04.94Z'},
  {source: 'CORPORATE_EARNINGS', sourceLabel: 'CausalFactor', target: 'SEMICONDUCTOR_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.35, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:04.94Z'},
  {source: 'CORPORATE_EARNINGS', sourceLabel: 'CausalFactor', target: 'SOFTWARE_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.35, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:04.94Z'},
  {source: 'CORPORATE_EARNINGS', sourceLabel: 'CausalFactor', target: 'TELECOM', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.35, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:04.94Z'},
  {source: 'CORPORATE_EARNINGS', sourceLabel: 'CausalFactor', target: 'WEAPON_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.35, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:04.94Z'},
  {source: 'CURRENCY_CRISIS', sourceLabel: 'CausalFactor', target: 'BANKING_FINANCE', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.45, confidence: 0.55, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.297Z'},
  {source: 'CURRENCY_CRISIS', sourceLabel: 'CausalFactor', target: 'PRECIOUS_METALS', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.5, confidence: 0.6, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.317Z'},
  {source: 'CYBERSECURITY_INCIDENT', sourceLabel: 'CausalFactor', target: 'AEROSPACE_AVIATION', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.106Z'},
  {source: 'CYBERSECURITY_INCIDENT', sourceLabel: 'CausalFactor', target: 'AUTOMOTIVE_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.106Z'},
  {source: 'CYBERSECURITY_INCIDENT', sourceLabel: 'CausalFactor', target: 'BANKING_FINANCE', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.106Z'},
  {source: 'CYBERSECURITY_INCIDENT', sourceLabel: 'CausalFactor', target: 'CONSUMER_ELECTRONICS', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.106Z'},
  {source: 'CYBERSECURITY_INCIDENT', sourceLabel: 'CausalFactor', target: 'FOOD_INGREDIENTS', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.106Z'},
  {source: 'CYBERSECURITY_INCIDENT', sourceLabel: 'CausalFactor', target: 'INDUSTRIAL_MANUFACTURING', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.106Z'},
  {source: 'CYBERSECURITY_INCIDENT', sourceLabel: 'CausalFactor', target: 'INTERNET_PLATFORMS', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.106Z'},
  {source: 'CYBERSECURITY_INCIDENT', sourceLabel: 'CausalFactor', target: 'OIL_GAS', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.106Z'},
  {source: 'CYBERSECURITY_INCIDENT', sourceLabel: 'CausalFactor', target: 'PHARMA_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.106Z'},
  {source: 'CYBERSECURITY_INCIDENT', sourceLabel: 'CausalFactor', target: 'PRECIOUS_METALS', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.106Z'},
  {source: 'CYBERSECURITY_INCIDENT', sourceLabel: 'CausalFactor', target: 'SEMICONDUCTOR_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.106Z'},
  {source: 'CYBERSECURITY_INCIDENT', sourceLabel: 'CausalFactor', target: 'SOFTWARE_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.106Z'},
  {source: 'CYBERSECURITY_INCIDENT', sourceLabel: 'CausalFactor', target: 'TELECOM', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.106Z'},
  {source: 'CYBERSECURITY_INCIDENT', sourceLabel: 'CausalFactor', target: 'WEAPON_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.106Z'},
  {source: 'DEBT_CRISIS', sourceLabel: 'CausalFactor', target: 'AEROSPACE_AVIATION', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.55, confidence: 0.65, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.871Z'},
  {source: 'DEBT_CRISIS', sourceLabel: 'CausalFactor', target: 'AUTOMOTIVE_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.55, confidence: 0.65, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.871Z'},
  {source: 'DEBT_CRISIS', sourceLabel: 'CausalFactor', target: 'BANKING_FINANCE', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.55, confidence: 0.65, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.871Z'},
  {source: 'DEBT_CRISIS', sourceLabel: 'CausalFactor', target: 'CONSUMER_ELECTRONICS', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.55, confidence: 0.65, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.871Z'},
  {source: 'DEBT_CRISIS', sourceLabel: 'CausalFactor', target: 'FOOD_INGREDIENTS', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.55, confidence: 0.65, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.871Z'},
  {source: 'DEBT_CRISIS', sourceLabel: 'CausalFactor', target: 'INDUSTRIAL_MANUFACTURING', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.55, confidence: 0.65, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.871Z'},
  {source: 'DEBT_CRISIS', sourceLabel: 'CausalFactor', target: 'INTERNET_PLATFORMS', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.55, confidence: 0.65, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.871Z'},
  {source: 'DEBT_CRISIS', sourceLabel: 'CausalFactor', target: 'OIL_GAS', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.55, confidence: 0.65, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.871Z'},
  {source: 'DEBT_CRISIS', sourceLabel: 'CausalFactor', target: 'PHARMA_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.55, confidence: 0.65, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.871Z'},
  {source: 'DEBT_CRISIS', sourceLabel: 'CausalFactor', target: 'PRECIOUS_METALS', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.55, confidence: 0.65, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.871Z'},
  {source: 'DEBT_CRISIS', sourceLabel: 'CausalFactor', target: 'SEMICONDUCTOR_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.55, confidence: 0.65, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.871Z'},
  {source: 'DEBT_CRISIS', sourceLabel: 'CausalFactor', target: 'SOFTWARE_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.55, confidence: 0.65, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.871Z'},
  {source: 'DEBT_CRISIS', sourceLabel: 'CausalFactor', target: 'TELECOM', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.55, confidence: 0.65, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.871Z'},
  {source: 'DEBT_CRISIS', sourceLabel: 'CausalFactor', target: 'WEAPON_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.55, confidence: 0.65, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.871Z'},
  {source: 'DIVIDEND_CHANGE', sourceLabel: 'CausalFactor', target: 'AEROSPACE_AVIATION', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.983Z'},
  {source: 'DIVIDEND_CHANGE', sourceLabel: 'CausalFactor', target: 'AUTOMOTIVE_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.983Z'},
  {source: 'DIVIDEND_CHANGE', sourceLabel: 'CausalFactor', target: 'BANKING_FINANCE', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.983Z'},
  {source: 'DIVIDEND_CHANGE', sourceLabel: 'CausalFactor', target: 'CONSUMER_ELECTRONICS', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.983Z'},
  {source: 'DIVIDEND_CHANGE', sourceLabel: 'CausalFactor', target: 'FOOD_INGREDIENTS', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.983Z'},
  {source: 'DIVIDEND_CHANGE', sourceLabel: 'CausalFactor', target: 'INDUSTRIAL_MANUFACTURING', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.983Z'},
  {source: 'DIVIDEND_CHANGE', sourceLabel: 'CausalFactor', target: 'INTERNET_PLATFORMS', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.983Z'},
  {source: 'DIVIDEND_CHANGE', sourceLabel: 'CausalFactor', target: 'OIL_GAS', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.983Z'},
  {source: 'DIVIDEND_CHANGE', sourceLabel: 'CausalFactor', target: 'PHARMA_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.983Z'},
  {source: 'DIVIDEND_CHANGE', sourceLabel: 'CausalFactor', target: 'PRECIOUS_METALS', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.983Z'},
  {source: 'DIVIDEND_CHANGE', sourceLabel: 'CausalFactor', target: 'SEMICONDUCTOR_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.983Z'},
  {source: 'DIVIDEND_CHANGE', sourceLabel: 'CausalFactor', target: 'SOFTWARE_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.983Z'},
  {source: 'DIVIDEND_CHANGE', sourceLabel: 'CausalFactor', target: 'TELECOM', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.983Z'},
  {source: 'DIVIDEND_CHANGE', sourceLabel: 'CausalFactor', target: 'WEAPON_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.983Z'},
  {source: 'ECONOMIC_DATA_RELEASE', sourceLabel: 'CausalFactor', target: 'BANKING_FINANCE', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.562Z'},
  {source: 'ECONOMIC_DATA_RELEASE', sourceLabel: 'CausalFactor', target: 'INDUSTRIAL_MANUFACTURING', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.602Z'},
  {source: 'ENERGY_POLICY', sourceLabel: 'CausalFactor', target: 'AUTOMOTIVE_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.736Z'},
  {source: 'ENERGY_POLICY', sourceLabel: 'CausalFactor', target: 'INDUSTRIAL_MANUFACTURING', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.757Z'},
  {source: 'ENERGY_POLICY', sourceLabel: 'CausalFactor', target: 'OIL_GAS', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.45, confidence: 0.55, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.716Z'},
  {source: 'ENERGY_POLICY', sourceLabel: 'CausalFactor', target: 'SEMICONDUCTOR_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.775Z'},
  {source: 'EXECUTIVE_CHANGE', sourceLabel: 'CausalFactor', target: 'AEROSPACE_AVIATION', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.828Z'},
  {source: 'EXECUTIVE_CHANGE', sourceLabel: 'CausalFactor', target: 'AUTOMOTIVE_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.828Z'},
  {source: 'EXECUTIVE_CHANGE', sourceLabel: 'CausalFactor', target: 'BANKING_FINANCE', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.828Z'},
  {source: 'EXECUTIVE_CHANGE', sourceLabel: 'CausalFactor', target: 'CONSUMER_ELECTRONICS', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.27999999999999997, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:14.457Z'},
  {source: 'EXECUTIVE_CHANGE', sourceLabel: 'CausalFactor', target: 'FOOD_INGREDIENTS', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.828Z'},
  {source: 'EXECUTIVE_CHANGE', sourceLabel: 'CausalFactor', target: 'INDUSTRIAL_MANUFACTURING', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.828Z'},
  {source: 'EXECUTIVE_CHANGE', sourceLabel: 'CausalFactor', target: 'INTERNET_PLATFORMS', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.828Z'},
  {source: 'EXECUTIVE_CHANGE', sourceLabel: 'CausalFactor', target: 'OIL_GAS', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.828Z'},
  {source: 'EXECUTIVE_CHANGE', sourceLabel: 'CausalFactor', target: 'PHARMA_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.828Z'},
  {source: 'EXECUTIVE_CHANGE', sourceLabel: 'CausalFactor', target: 'PRECIOUS_METALS', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.828Z'},
  {source: 'EXECUTIVE_CHANGE', sourceLabel: 'CausalFactor', target: 'SEMICONDUCTOR_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.828Z'},
  {source: 'EXECUTIVE_CHANGE', sourceLabel: 'CausalFactor', target: 'SOFTWARE_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.828Z'},
  {source: 'EXECUTIVE_CHANGE', sourceLabel: 'CausalFactor', target: 'TELECOM', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.828Z'},
  {source: 'EXECUTIVE_CHANGE', sourceLabel: 'CausalFactor', target: 'WEAPON_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.828Z'},
  {source: 'FISCAL_POLICY', sourceLabel: 'CausalFactor', target: 'BANKING_FINANCE', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.4, confidence: 0.55, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.217Z'},
  {source: 'FISCAL_POLICY', sourceLabel: 'CausalFactor', target: 'INDUSTRIAL_MANUFACTURING', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.276Z'},
  {source: 'GEOPOLITICAL_TENSION', sourceLabel: 'CausalFactor', target: 'AEROSPACE_AVIATION', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.456Z'},
  {source: 'GEOPOLITICAL_TENSION', sourceLabel: 'CausalFactor', target: 'OIL_GAS', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.4, confidence: 0.55, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.441Z'},
  {source: 'GEOPOLITICAL_TENSION', sourceLabel: 'CausalFactor', target: 'PRECIOUS_METALS', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.5, confidence: 0.6, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.425Z'},
  {source: 'GEOPOLITICAL_TENSION', sourceLabel: 'CausalFactor', target: 'WEAPON_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.55, confidence: 0.65, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.403Z'},
  {source: 'INFLATION_CHANGE', sourceLabel: 'CausalFactor', target: 'FOOD_INGREDIENTS', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:04.861Z'},
  {source: 'INFLATION_CHANGE', sourceLabel: 'CausalFactor', target: 'PRECIOUS_METALS', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.45, confidence: 0.55, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:04.676Z'},
  {source: 'IPO_LISTING', sourceLabel: 'CausalFactor', target: 'AEROSPACE_AVIATION', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.25, confidence: 0.4, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.075Z'},
  {source: 'IPO_LISTING', sourceLabel: 'CausalFactor', target: 'AUTOMOTIVE_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.25, confidence: 0.4, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.075Z'},
  {source: 'IPO_LISTING', sourceLabel: 'CausalFactor', target: 'BANKING_FINANCE', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.25, confidence: 0.4, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.075Z'},
  {source: 'IPO_LISTING', sourceLabel: 'CausalFactor', target: 'CONSUMER_ELECTRONICS', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.25, confidence: 0.4, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.075Z'},
  {source: 'IPO_LISTING', sourceLabel: 'CausalFactor', target: 'FOOD_INGREDIENTS', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.25, confidence: 0.4, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.075Z'},
  {source: 'IPO_LISTING', sourceLabel: 'CausalFactor', target: 'INDUSTRIAL_MANUFACTURING', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.25, confidence: 0.4, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.075Z'},
  {source: 'IPO_LISTING', sourceLabel: 'CausalFactor', target: 'INTERNET_PLATFORMS', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.25, confidence: 0.4, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.075Z'},
  {source: 'IPO_LISTING', sourceLabel: 'CausalFactor', target: 'OIL_GAS', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.25, confidence: 0.4, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.075Z'},
  {source: 'IPO_LISTING', sourceLabel: 'CausalFactor', target: 'PHARMA_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.25, confidence: 0.4, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.075Z'},
  {source: 'IPO_LISTING', sourceLabel: 'CausalFactor', target: 'PRECIOUS_METALS', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.25, confidence: 0.4, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.075Z'},
  {source: 'IPO_LISTING', sourceLabel: 'CausalFactor', target: 'SEMICONDUCTOR_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.25, confidence: 0.4, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.075Z'},
  {source: 'IPO_LISTING', sourceLabel: 'CausalFactor', target: 'SOFTWARE_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.25, confidence: 0.4, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.075Z'},
  {source: 'IPO_LISTING', sourceLabel: 'CausalFactor', target: 'TELECOM', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.25, confidence: 0.4, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.075Z'},
  {source: 'IPO_LISTING', sourceLabel: 'CausalFactor', target: 'WEAPON_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.25, confidence: 0.4, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.075Z'},
  {source: 'LEGAL_DISPUTE', sourceLabel: 'CausalFactor', target: 'AEROSPACE_AVIATION', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.921Z'},
  {source: 'LEGAL_DISPUTE', sourceLabel: 'CausalFactor', target: 'AUTOMOTIVE_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.921Z'},
  {source: 'LEGAL_DISPUTE', sourceLabel: 'CausalFactor', target: 'BANKING_FINANCE', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.921Z'},
  {source: 'LEGAL_DISPUTE', sourceLabel: 'CausalFactor', target: 'CONSUMER_ELECTRONICS', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.921Z'},
  {source: 'LEGAL_DISPUTE', sourceLabel: 'CausalFactor', target: 'FOOD_INGREDIENTS', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.921Z'},
  {source: 'LEGAL_DISPUTE', sourceLabel: 'CausalFactor', target: 'INDUSTRIAL_MANUFACTURING', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.921Z'},
  {source: 'LEGAL_DISPUTE', sourceLabel: 'CausalFactor', target: 'INTERNET_PLATFORMS', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.921Z'},
  {source: 'LEGAL_DISPUTE', sourceLabel: 'CausalFactor', target: 'OIL_GAS', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.921Z'},
  {source: 'LEGAL_DISPUTE', sourceLabel: 'CausalFactor', target: 'PHARMA_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.921Z'},
  {source: 'LEGAL_DISPUTE', sourceLabel: 'CausalFactor', target: 'PRECIOUS_METALS', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.921Z'},
  {source: 'LEGAL_DISPUTE', sourceLabel: 'CausalFactor', target: 'SEMICONDUCTOR_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.921Z'},
  {source: 'LEGAL_DISPUTE', sourceLabel: 'CausalFactor', target: 'SOFTWARE_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.921Z'},
  {source: 'LEGAL_DISPUTE', sourceLabel: 'CausalFactor', target: 'TELECOM', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.921Z'},
  {source: 'LEGAL_DISPUTE', sourceLabel: 'CausalFactor', target: 'WEAPON_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.921Z'},
  {source: 'MILITARY_CONFLICT', sourceLabel: 'CausalFactor', target: 'LMT_NYSE', targetLabel: 'Asset', condition: 'SAFE_HAVEN_ONLY', direction: 'DOWN', weight: 0.05, confidence: 0.7213114754098361, alpha: 45.0, beta: 18.0, last_updated: '2026-08-26T22:07:17.594Z'},
  {source: 'MILITARY_CONFLICT', sourceLabel: 'CausalFactor', target: 'NEM_NYSE', targetLabel: 'Asset', condition: 'SAFE_HAVEN_ONLY', direction: 'DOWN', weight: 0.2720615227400632, confidence: 0.6923076923076923, alpha: 37.0, beta: 17.0, last_updated: '2026-08-26T22:07:17.55Z'},
  {source: 'MILITARY_CONFLICT', sourceLabel: 'CausalFactor', target: 'SAAB_B_STO', targetLabel: 'Asset', condition: 'SAFE_HAVEN_ONLY', direction: 'UP', weight: 0.16022355870577068, confidence: 0.9014084507042254, alpha: 65.0, beta: 8.0, last_updated: '2026-08-25T21:07:19.116Z'},
  {source: 'MILITARY_CONFLICT', sourceLabel: 'CausalFactor', target: 'XOM_NYSE', targetLabel: 'Asset', condition: 'SAFE_HAVEN_ONLY', direction: 'DOWN', weight: 0.0663310316932878, confidence: 0.88, alpha: 45.0, beta: 7.0, last_updated: '2026-08-29T12:11:30.475Z'},
  {source: 'MILITARY_CONFLICT', sourceLabel: 'CausalFactor', target: 'XOM_NYSE', targetLabel: 'Asset', condition: 'TRANSPORT_AFFECTED', direction: 'UP', weight: 0.05243295184646282, confidence: 1.0, alpha: 3.0, beta: 1.0, last_updated: '2026-08-29T21:05:35.86Z'},
  {source: 'MILITARY_CONFLICT', sourceLabel: 'CausalFactor', target: 'AEROSPACE_AVIATION', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.4, confidence: 0.55, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:04.554Z'},
  {source: 'MILITARY_CONFLICT', sourceLabel: 'CausalFactor', target: 'OIL_GAS', targetLabel: 'AssetGroup', condition: 'TRANSPORT_AFFECTED', direction: 'UP', weight: 0.5, confidence: 0.6, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:02.622Z'},
  {source: 'MILITARY_CONFLICT', sourceLabel: 'CausalFactor', target: 'PRECIOUS_METALS', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.5, confidence: 0.6, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:04.657Z'},
  {source: 'MILITARY_CONFLICT', sourceLabel: 'CausalFactor', target: 'WEAPON_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.6, confidence: 0.65, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:04.437Z'},
  {source: 'NATURAL_DISASTER', sourceLabel: 'CausalFactor', target: 'PHARMA_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.35, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:04.846Z'},
  {source: 'PANDEMIC_OUTBREAK', sourceLabel: 'CausalFactor', target: 'AEROSPACE_AVIATION', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.6, confidence: 0.7, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.65Z'},
  {source: 'PANDEMIC_OUTBREAK', sourceLabel: 'CausalFactor', target: 'PHARMA_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.55, confidence: 0.65, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.622Z'},
  {source: 'PANDEMIC_OUTBREAK', sourceLabel: 'CausalFactor', target: 'PRECIOUS_METALS', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.5, confidence: 0.6, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.691Z'},
  {source: 'POLITICAL_TRANSITION', sourceLabel: 'CausalFactor', target: 'NEM_NYSE', targetLabel: 'Asset', condition: 'SAFE_HAVEN_ONLY', direction: 'DOWN', weight: 0.3801626935833473, confidence: 0.6666666666666666, alpha: 17.0, beta: 9.0, last_updated: '2026-08-17T07:10:26.664Z'},
  {source: 'POLITICAL_TRANSITION', sourceLabel: 'CausalFactor', target: 'WEAPON_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:04.538Z'},
  {source: 'PRODUCT_RECALL', sourceLabel: 'CausalFactor', target: 'AEROSPACE_AVIATION', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.4, confidence: 0.55, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.955Z'},
  {source: 'PRODUCT_RECALL', sourceLabel: 'CausalFactor', target: 'AUTOMOTIVE_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.4, confidence: 0.55, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.955Z'},
  {source: 'PRODUCT_RECALL', sourceLabel: 'CausalFactor', target: 'BANKING_FINANCE', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.4, confidence: 0.55, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.955Z'},
  {source: 'PRODUCT_RECALL', sourceLabel: 'CausalFactor', target: 'CONSUMER_ELECTRONICS', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.4, confidence: 0.55, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.955Z'},
  {source: 'PRODUCT_RECALL', sourceLabel: 'CausalFactor', target: 'FOOD_INGREDIENTS', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.4, confidence: 0.55, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.955Z'},
  {source: 'PRODUCT_RECALL', sourceLabel: 'CausalFactor', target: 'INDUSTRIAL_MANUFACTURING', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.4, confidence: 0.55, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.955Z'},
  {source: 'PRODUCT_RECALL', sourceLabel: 'CausalFactor', target: 'INTERNET_PLATFORMS', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.4, confidence: 0.55, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.955Z'},
  {source: 'PRODUCT_RECALL', sourceLabel: 'CausalFactor', target: 'OIL_GAS', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.4, confidence: 0.55, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.955Z'},
  {source: 'PRODUCT_RECALL', sourceLabel: 'CausalFactor', target: 'PHARMA_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.4, confidence: 0.55, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.955Z'},
  {source: 'PRODUCT_RECALL', sourceLabel: 'CausalFactor', target: 'PRECIOUS_METALS', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.4, confidence: 0.55, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.955Z'},
  {source: 'PRODUCT_RECALL', sourceLabel: 'CausalFactor', target: 'SEMICONDUCTOR_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.4, confidence: 0.55, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.955Z'},
  {source: 'PRODUCT_RECALL', sourceLabel: 'CausalFactor', target: 'SOFTWARE_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.4, confidence: 0.55, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.955Z'},
  {source: 'PRODUCT_RECALL', sourceLabel: 'CausalFactor', target: 'TELECOM', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.4, confidence: 0.55, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.955Z'},
  {source: 'PRODUCT_RECALL', sourceLabel: 'CausalFactor', target: 'WEAPON_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.4, confidence: 0.55, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.955Z'},
  {source: 'RATE_DECISION', sourceLabel: 'CausalFactor', target: 'BANKING_FINANCE', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.45, confidence: 0.6, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:04.72Z'},
  {source: 'RATE_DECISION', sourceLabel: 'CausalFactor', target: 'INTERNET_PLATFORMS', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:04.748Z'},
  {source: 'RATE_DECISION', sourceLabel: 'CausalFactor', target: 'SOFTWARE_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:04.734Z'},
  {source: 'RATE_DECISION', sourceLabel: 'CausalFactor', target: 'TELECOM', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:04.889Z'},
  {source: 'RECESSION_SIGNAL', sourceLabel: 'CausalFactor', target: 'AUTOMOTIVE_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.5, confidence: 0.6, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:04.763Z'},
  {source: 'RECESSION_SIGNAL', sourceLabel: 'CausalFactor', target: 'CONSUMER_ELECTRONICS', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.45, confidence: 0.55, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:04.792Z'},
  {source: 'RECESSION_SIGNAL', sourceLabel: 'CausalFactor', target: 'INDUSTRIAL_MANUFACTURING', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.5, confidence: 0.6, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:04.777Z'},
  {source: 'RECESSION_SIGNAL', sourceLabel: 'CausalFactor', target: 'OIL_GAS', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.45, confidence: 0.6, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:04.639Z'},
  {source: 'RECESSION_SIGNAL', sourceLabel: 'CausalFactor', target: 'PHARMA_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:04.832Z'},
  {source: 'REGULATORY_ACTION', sourceLabel: 'CausalFactor', target: 'AEROSPACE_AVIATION', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.853Z'},
  {source: 'REGULATORY_ACTION', sourceLabel: 'CausalFactor', target: 'AUTOMOTIVE_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.853Z'},
  {source: 'REGULATORY_ACTION', sourceLabel: 'CausalFactor', target: 'BANKING_FINANCE', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.853Z'},
  {source: 'REGULATORY_ACTION', sourceLabel: 'CausalFactor', target: 'CONSUMER_ELECTRONICS', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.853Z'},
  {source: 'REGULATORY_ACTION', sourceLabel: 'CausalFactor', target: 'FOOD_INGREDIENTS', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.853Z'},
  {source: 'REGULATORY_ACTION', sourceLabel: 'CausalFactor', target: 'INDUSTRIAL_MANUFACTURING', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.853Z'},
  {source: 'REGULATORY_ACTION', sourceLabel: 'CausalFactor', target: 'INTERNET_PLATFORMS', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.853Z'},
  {source: 'REGULATORY_ACTION', sourceLabel: 'CausalFactor', target: 'OIL_GAS', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.853Z'},
  {source: 'REGULATORY_ACTION', sourceLabel: 'CausalFactor', target: 'PHARMA_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.853Z'},
  {source: 'REGULATORY_ACTION', sourceLabel: 'CausalFactor', target: 'PRECIOUS_METALS', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.853Z'},
  {source: 'REGULATORY_ACTION', sourceLabel: 'CausalFactor', target: 'SEMICONDUCTOR_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.853Z'},
  {source: 'REGULATORY_ACTION', sourceLabel: 'CausalFactor', target: 'SOFTWARE_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.853Z'},
  {source: 'REGULATORY_ACTION', sourceLabel: 'CausalFactor', target: 'TELECOM', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.853Z'},
  {source: 'REGULATORY_ACTION', sourceLabel: 'CausalFactor', target: 'WEAPON_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.853Z'},
  {source: 'RESTRUCTURING', sourceLabel: 'CausalFactor', target: 'AEROSPACE_AVIATION', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.894Z'},
  {source: 'RESTRUCTURING', sourceLabel: 'CausalFactor', target: 'AUTOMOTIVE_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.894Z'},
  {source: 'RESTRUCTURING', sourceLabel: 'CausalFactor', target: 'BANKING_FINANCE', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.894Z'},
  {source: 'RESTRUCTURING', sourceLabel: 'CausalFactor', target: 'CONSUMER_ELECTRONICS', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.894Z'},
  {source: 'RESTRUCTURING', sourceLabel: 'CausalFactor', target: 'FOOD_INGREDIENTS', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.894Z'},
  {source: 'RESTRUCTURING', sourceLabel: 'CausalFactor', target: 'INDUSTRIAL_MANUFACTURING', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.894Z'},
  {source: 'RESTRUCTURING', sourceLabel: 'CausalFactor', target: 'INTERNET_PLATFORMS', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.894Z'},
  {source: 'RESTRUCTURING', sourceLabel: 'CausalFactor', target: 'OIL_GAS', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.894Z'},
  {source: 'RESTRUCTURING', sourceLabel: 'CausalFactor', target: 'PHARMA_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.894Z'},
  {source: 'RESTRUCTURING', sourceLabel: 'CausalFactor', target: 'PRECIOUS_METALS', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.894Z'},
  {source: 'RESTRUCTURING', sourceLabel: 'CausalFactor', target: 'SEMICONDUCTOR_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.894Z'},
  {source: 'RESTRUCTURING', sourceLabel: 'CausalFactor', target: 'SOFTWARE_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.894Z'},
  {source: 'RESTRUCTURING', sourceLabel: 'CausalFactor', target: 'TELECOM', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.894Z'},
  {source: 'RESTRUCTURING', sourceLabel: 'CausalFactor', target: 'WEAPON_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:06.894Z'},
  {source: 'SANCTIONS', sourceLabel: 'CausalFactor', target: 'NEM_NYSE', targetLabel: 'Asset', condition: 'SAFE_HAVEN_ONLY', direction: 'DOWN', weight: 0.5083883386743066, confidence: 0.8, alpha: 5.0, beta: 2.0, last_updated: '2026-08-25T10:09:12.179Z'},
  {source: 'SANCTIONS', sourceLabel: 'CausalFactor', target: 'SAAB_B_STO', targetLabel: 'Asset', condition: 'SAFE_HAVEN_ONLY', direction: 'UP', weight: 0.263809028318088, confidence: 1.0, alpha: 3.0, beta: 1.0, last_updated: '2026-08-14T17:24:47.108Z'},
  {source: 'SANCTIONS', sourceLabel: 'CausalFactor', target: 'XOM_NYSE', targetLabel: 'Asset', condition: 'SAFE_HAVEN_ONLY', direction: 'DOWN', weight: 0.14104017126306567, confidence: 1.0, alpha: 3.0, beta: 1.0, last_updated: '2026-08-14T17:24:47.202Z'},
  {source: 'SANCTIONS', sourceLabel: 'CausalFactor', target: 'SEMICONDUCTOR_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.5, confidence: 0.6, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:04.705Z'},
  {source: 'SANCTIONS', sourceLabel: 'CausalFactor', target: 'TELECOM', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:04.903Z'},
  {source: 'SANCTIONS', sourceLabel: 'CausalFactor', target: 'WEAPON_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.4, confidence: 0.55, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:04.497Z'},
  {source: 'SHARE_BUYBACK', sourceLabel: 'CausalFactor', target: 'AEROSPACE_AVIATION', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.036Z'},
  {source: 'SHARE_BUYBACK', sourceLabel: 'CausalFactor', target: 'AUTOMOTIVE_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.036Z'},
  {source: 'SHARE_BUYBACK', sourceLabel: 'CausalFactor', target: 'BANKING_FINANCE', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.036Z'},
  {source: 'SHARE_BUYBACK', sourceLabel: 'CausalFactor', target: 'CONSUMER_ELECTRONICS', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.036Z'},
  {source: 'SHARE_BUYBACK', sourceLabel: 'CausalFactor', target: 'FOOD_INGREDIENTS', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.036Z'},
  {source: 'SHARE_BUYBACK', sourceLabel: 'CausalFactor', target: 'INDUSTRIAL_MANUFACTURING', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.036Z'},
  {source: 'SHARE_BUYBACK', sourceLabel: 'CausalFactor', target: 'INTERNET_PLATFORMS', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.036Z'},
  {source: 'SHARE_BUYBACK', sourceLabel: 'CausalFactor', target: 'OIL_GAS', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.036Z'},
  {source: 'SHARE_BUYBACK', sourceLabel: 'CausalFactor', target: 'PHARMA_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.036Z'},
  {source: 'SHARE_BUYBACK', sourceLabel: 'CausalFactor', target: 'PRECIOUS_METALS', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.036Z'},
  {source: 'SHARE_BUYBACK', sourceLabel: 'CausalFactor', target: 'SEMICONDUCTOR_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.036Z'},
  {source: 'SHARE_BUYBACK', sourceLabel: 'CausalFactor', target: 'SOFTWARE_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.036Z'},
  {source: 'SHARE_BUYBACK', sourceLabel: 'CausalFactor', target: 'TELECOM', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.036Z'},
  {source: 'SHARE_BUYBACK', sourceLabel: 'CausalFactor', target: 'WEAPON_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.3, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.036Z'},
  {source: 'SOVEREIGN_DEBT', sourceLabel: 'CausalFactor', target: 'BANKING_FINANCE', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.55, confidence: 0.65, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.333Z'},
  {source: 'SOVEREIGN_DEBT', sourceLabel: 'CausalFactor', target: 'PRECIOUS_METALS', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.55, confidence: 0.65, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.384Z'},
  {source: 'STRAIT_CLOSURE', sourceLabel: 'CausalFactor', target: 'XOM_NYSE', targetLabel: 'Asset', condition: 'TRANSPORT_AFFECTED', direction: 'DOWN', weight: 0.16104017126306566, confidence: 1.0, alpha: 3.0, beta: 1.0, last_updated: '2026-08-29T20:58:14.727Z'},
  {source: 'STRAIT_CLOSURE', sourceLabel: 'CausalFactor', target: 'OIL_GAS', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.55, confidence: 0.65, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:04.622Z'},
  {source: 'SUPPLY_DISRUPTION', sourceLabel: 'CausalFactor', target: 'AEROSPACE_AVIATION', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:04.569Z'},
  {source: 'SUPPLY_DISRUPTION', sourceLabel: 'CausalFactor', target: 'AUTOMOTIVE_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.4, confidence: 0.55, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:04.805Z'},
  {source: 'SUPPLY_DISRUPTION', sourceLabel: 'CausalFactor', target: 'FOOD_INGREDIENTS', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.4, confidence: 0.55, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:04.876Z'},
  {source: 'SUPPLY_DISRUPTION', sourceLabel: 'CausalFactor', target: 'INDUSTRIAL_MANUFACTURING', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.4, confidence: 0.55, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:04.818Z'},
  {source: 'SUPPLY_DISRUPTION', sourceLabel: 'CausalFactor', target: 'OIL_GAS', targetLabel: 'AssetGroup', condition: null, direction: 'UP', weight: 0.55, confidence: 0.65, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:04.583Z'},
  {source: 'SUPPLY_DISRUPTION', sourceLabel: 'CausalFactor', target: 'SEMICONDUCTOR_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.55, confidence: 0.65, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:04.691Z'},
  {source: 'TRADE_POLICY', sourceLabel: 'CausalFactor', target: 'AUTOMOTIVE_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.45, confidence: 0.55, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.134Z'},
  {source: 'TRADE_POLICY', sourceLabel: 'CausalFactor', target: 'INDUSTRIAL_MANUFACTURING', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.4, confidence: 0.55, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.177Z'},
  {source: 'TRADE_POLICY', sourceLabel: 'CausalFactor', target: 'OIL_GAS', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.35, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.193Z'},
  {source: 'TRADE_POLICY', sourceLabel: 'CausalFactor', target: 'SEMICONDUCTOR_INDUSTRY', targetLabel: 'AssetGroup', condition: null, direction: 'DOWN', weight: 0.5, confidence: 0.6, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:07.153Z'}
] AS row
MATCH (s:CausalFactor {id: row.source})
MATCH (t) WHERE t.id = row.target AND row.targetLabel IN labels(t)
OPTIONAL MATCH (s)-[e:CAUSES]->(t)
  WHERE (e.condition IS NULL AND row.condition IS NULL) OR e.condition = row.condition
WITH row, s, t, collect(e) AS existing
FOREACH (_ IN CASE WHEN size(existing) = 0 THEN [1] ELSE [] END |
  CREATE (s)-[:CAUSES {condition: row.condition, direction: row.direction,
                       weight: row.weight, confidence: row.confidence,
                       alpha: row.alpha, beta: row.beta,
                       last_updated: datetime(row.last_updated)}]->(t))
FOREACH (r IN existing |
  SET r.direction = row.direction, r.weight = row.weight, r.confidence = row.confidence,
      r.alpha = row.alpha, r.beta = row.beta,
      r.last_updated = datetime(row.last_updated));

// ==========================================================================
// section: CORRELATES_WITH
// ==========================================================================
UNWIND [
  {source: 'LUG_STO', sourceLabel: 'Asset', target: 'SWED_A_STO', targetLabel: 'Asset', condition: 'UPSTREAM_DOWN', direction: 'DOWN', weight: 0.15, confidence: 0.4, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:09.913Z'},
  {source: 'NEM_NYSE', sourceLabel: 'Asset', target: 'SWED_A_STO', targetLabel: 'Asset', condition: 'UPSTREAM_DOWN', direction: 'UP', weight: 0.4, confidence: 0.45, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:09.899Z'},
  {source: 'NEM_NYSE', sourceLabel: 'Asset', target: 'XOM_NYSE', targetLabel: 'Asset', condition: 'UPSTREAM_UP', direction: 'DOWN', weight: 0.3, confidence: 0.5, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:09.931Z'},
  {source: 'XOM_NYSE', sourceLabel: 'Asset', target: 'LUG_STO', targetLabel: 'Asset', condition: 'UPSTREAM_UP', direction: 'DOWN', weight: 0.35, confidence: 0.55, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:09.877Z'},
  {source: 'XOM_NYSE', sourceLabel: 'Asset', target: 'NEM_NYSE', targetLabel: 'Asset', condition: 'UPSTREAM_UP', direction: 'DOWN', weight: 0.45, confidence: 0.6, alpha: 1.0, beta: 1.0, last_updated: '2026-08-29T20:58:09.823Z'}
] AS row
MATCH (s:Asset {id: row.source})
MATCH (t:Asset {id: row.target})
OPTIONAL MATCH (s)-[e:CORRELATES_WITH]->(t)
  WHERE (e.condition IS NULL AND row.condition IS NULL) OR e.condition = row.condition
WITH row, s, t, collect(e) AS existing
FOREACH (_ IN CASE WHEN size(existing) = 0 THEN [1] ELSE [] END |
  CREATE (s)-[:CORRELATES_WITH {condition: row.condition, direction: row.direction,
                                weight: row.weight, confidence: row.confidence,
                                alpha: row.alpha, beta: row.beta,
                                last_updated: datetime(row.last_updated)}]->(t))
FOREACH (r IN existing |
  SET r.direction = row.direction, r.weight = row.weight, r.confidence = row.confidence,
      r.alpha = row.alpha, r.beta = row.beta,
      r.last_updated = datetime(row.last_updated));
