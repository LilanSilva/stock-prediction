// Causal edges for the 21 new event types added in taxonomy version 1.1.
//
// Design principles (same as 06-seed-group-edges.cypher):
//   - Company-level events (CORPORATE_ACQUISITION etc.) attach to ALL groups via a modest prior;
//     the offline learner refines per-company once outcomes accumulate.
//   - Macro/market events attach to the specific groups most reliably affected.
//   - Weights are deliberately conservative (0.30-0.55): these are expert guesses with no evidence.
//   - direction UP means the factor typically lifts the asset; DOWN means it typically depresses it.
//   - MERGE keeps this idempotent so the seed container can re-run safely.

// =============================================================================
// COMPANY-LEVEL EVENTS — attach to ALL groups (same pattern as CORPORATE_EARNINGS)
// =============================================================================

// CORPORATE_ACQUISITION: mergers/takeovers lift the target, mixed for acquirer.
// Modest UP prior: target premium effect dominates at group level.
MATCH (cf:CausalFactor {id: 'CORPORATE_ACQUISITION'}), (g:AssetGroup)
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'UP', r.weight = 0.40, r.confidence = 0.50, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

// EXECUTIVE_CHANGE: CEO/CFO departure creates uncertainty, modest DOWN prior.
MATCH (cf:CausalFactor {id: 'EXECUTIVE_CHANGE'}), (g:AssetGroup)
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'DOWN', r.weight = 0.30, r.confidence = 0.45, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

// REGULATORY_ACTION: fines/bans are negative; approvals positive. DOWN prior (enforcement is more common).
MATCH (cf:CausalFactor {id: 'REGULATORY_ACTION'}), (g:AssetGroup)
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'DOWN', r.weight = 0.35, r.confidence = 0.50, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

// DEBT_CRISIS: bankruptcy/insolvency strongly negative.
MATCH (cf:CausalFactor {id: 'DEBT_CRISIS'}), (g:AssetGroup)
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'DOWN', r.weight = 0.55, r.confidence = 0.65, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

// RESTRUCTURING: layoffs/spin-offs — mixed; modest DOWN (uncertainty, cost signal).
MATCH (cf:CausalFactor {id: 'RESTRUCTURING'}), (g:AssetGroup)
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'DOWN', r.weight = 0.30, r.confidence = 0.45, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

// LEGAL_DISPUTE: lawsuits/investigations are negative.
MATCH (cf:CausalFactor {id: 'LEGAL_DISPUTE'}), (g:AssetGroup)
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'DOWN', r.weight = 0.35, r.confidence = 0.50, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

// PRODUCT_RECALL: safety issues are negative.
MATCH (cf:CausalFactor {id: 'PRODUCT_RECALL'}), (g:AssetGroup)
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'DOWN', r.weight = 0.40, r.confidence = 0.55, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

// DIVIDEND_CHANGE: cuts are negative, raises positive. DOWN prior (cuts are more newsworthy).
MATCH (cf:CausalFactor {id: 'DIVIDEND_CHANGE'}), (g:AssetGroup)
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'DOWN', r.weight = 0.30, r.confidence = 0.45, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

// CONTRACT_WIN: new deals/partnerships are positive.
MATCH (cf:CausalFactor {id: 'CONTRACT_WIN'}), (g:AssetGroup)
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'UP', r.weight = 0.35, r.confidence = 0.50, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

// SHARE_BUYBACK: buybacks signal management confidence, modestly positive.
MATCH (cf:CausalFactor {id: 'SHARE_BUYBACK'}), (g:AssetGroup)
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'UP', r.weight = 0.30, r.confidence = 0.45, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

// IPO_LISTING: new listings — neutral to modestly positive for the sector.
MATCH (cf:CausalFactor {id: 'IPO_LISTING'}), (g:AssetGroup)
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'UP', r.weight = 0.25, r.confidence = 0.40, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

// CYBERSECURITY_INCIDENT: data breaches/ransomware are negative.
MATCH (cf:CausalFactor {id: 'CYBERSECURITY_INCIDENT'}), (g:AssetGroup)
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'DOWN', r.weight = 0.35, r.confidence = 0.50, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

// =============================================================================
// MACRO / COUNTRY-LEVEL EVENTS — targeted group edges
// =============================================================================

// TRADE_POLICY: tariffs hurt exporters, manufacturers, tech supply chains.
MATCH (cf:CausalFactor {id: 'TRADE_POLICY'}), (g:AssetGroup {id: 'AUTOMOTIVE_INDUSTRY'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'DOWN', r.weight = 0.45, r.confidence = 0.55, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

MATCH (cf:CausalFactor {id: 'TRADE_POLICY'}), (g:AssetGroup {id: 'SEMICONDUCTOR_INDUSTRY'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'DOWN', r.weight = 0.50, r.confidence = 0.60, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

MATCH (cf:CausalFactor {id: 'TRADE_POLICY'}), (g:AssetGroup {id: 'INDUSTRIAL_MANUFACTURING'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'DOWN', r.weight = 0.40, r.confidence = 0.55, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

MATCH (cf:CausalFactor {id: 'TRADE_POLICY'}), (g:AssetGroup {id: 'OIL_GAS'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'DOWN', r.weight = 0.35, r.confidence = 0.50, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

// FISCAL_POLICY: stimulus lifts cyclicals and rate-sensitives.
MATCH (cf:CausalFactor {id: 'FISCAL_POLICY'}), (g:AssetGroup {id: 'BANKING_FINANCE'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'UP', r.weight = 0.40, r.confidence = 0.55, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

MATCH (cf:CausalFactor {id: 'FISCAL_POLICY'}), (g:AssetGroup {id: 'REAL_ESTATE'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'UP', r.weight = 0.35, r.confidence = 0.50, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

MATCH (cf:CausalFactor {id: 'FISCAL_POLICY'}), (g:AssetGroup {id: 'INDUSTRIAL_MANUFACTURING'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'UP', r.weight = 0.35, r.confidence = 0.50, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

// CURRENCY_CRISIS: exporters benefit (cheaper currency); importers and banks hurt.
MATCH (cf:CausalFactor {id: 'CURRENCY_CRISIS'}), (g:AssetGroup {id: 'BANKING_FINANCE'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'DOWN', r.weight = 0.45, r.confidence = 0.55, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

MATCH (cf:CausalFactor {id: 'CURRENCY_CRISIS'}), (g:AssetGroup {id: 'PRECIOUS_METALS'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'UP', r.weight = 0.50, r.confidence = 0.60, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

// SOVEREIGN_DEBT: country default crushes banks and real estate; gold benefits.
MATCH (cf:CausalFactor {id: 'SOVEREIGN_DEBT'}), (g:AssetGroup {id: 'BANKING_FINANCE'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'DOWN', r.weight = 0.55, r.confidence = 0.65, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

MATCH (cf:CausalFactor {id: 'SOVEREIGN_DEBT'}), (g:AssetGroup {id: 'REAL_ESTATE'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'DOWN', r.weight = 0.45, r.confidence = 0.55, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

MATCH (cf:CausalFactor {id: 'SOVEREIGN_DEBT'}), (g:AssetGroup {id: 'PRECIOUS_METALS'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'UP', r.weight = 0.55, r.confidence = 0.65, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

// GEOPOLITICAL_TENSION: defence up; consumer discretionary down; gold up.
MATCH (cf:CausalFactor {id: 'GEOPOLITICAL_TENSION'}), (g:AssetGroup {id: 'WEAPON_INDUSTRY'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'UP', r.weight = 0.55, r.confidence = 0.65, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

MATCH (cf:CausalFactor {id: 'GEOPOLITICAL_TENSION'}), (g:AssetGroup {id: 'PRECIOUS_METALS'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'UP', r.weight = 0.50, r.confidence = 0.60, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

MATCH (cf:CausalFactor {id: 'GEOPOLITICAL_TENSION'}), (g:AssetGroup {id: 'OIL_GAS'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'UP', r.weight = 0.40, r.confidence = 0.55, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

MATCH (cf:CausalFactor {id: 'GEOPOLITICAL_TENSION'}), (g:AssetGroup {id: 'AEROSPACE_AVIATION'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'DOWN', r.weight = 0.35, r.confidence = 0.50, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

// COMMODITY_PRICE_SHOCK: oil price spike lifts producers, hurts consumers of energy.
MATCH (cf:CausalFactor {id: 'COMMODITY_PRICE_SHOCK'}), (g:AssetGroup {id: 'OIL_GAS'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'UP', r.weight = 0.60, r.confidence = 0.65, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

MATCH (cf:CausalFactor {id: 'COMMODITY_PRICE_SHOCK'}), (g:AssetGroup {id: 'PRECIOUS_METALS'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'UP', r.weight = 0.45, r.confidence = 0.55, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

MATCH (cf:CausalFactor {id: 'COMMODITY_PRICE_SHOCK'}), (g:AssetGroup {id: 'AUTOMOTIVE_INDUSTRY'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'DOWN', r.weight = 0.40, r.confidence = 0.55, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

MATCH (cf:CausalFactor {id: 'COMMODITY_PRICE_SHOCK'}), (g:AssetGroup {id: 'FOOD_INGREDIENTS'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'DOWN', r.weight = 0.45, r.confidence = 0.55, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

MATCH (cf:CausalFactor {id: 'COMMODITY_PRICE_SHOCK'}), (g:AssetGroup {id: 'AEROSPACE_AVIATION'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'DOWN', r.weight = 0.40, r.confidence = 0.55, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

// =============================================================================
// MARKET / FINANCIAL SYSTEM EVENTS
// =============================================================================

// ECONOMIC_DATA_RELEASE: GDP/PMI/jobs data moves rate-sensitive and cyclical sectors.
MATCH (cf:CausalFactor {id: 'ECONOMIC_DATA_RELEASE'}), (g:AssetGroup {id: 'BANKING_FINANCE'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'UP', r.weight = 0.35, r.confidence = 0.50, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

MATCH (cf:CausalFactor {id: 'ECONOMIC_DATA_RELEASE'}), (g:AssetGroup {id: 'REAL_ESTATE'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'UP', r.weight = 0.30, r.confidence = 0.45, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

MATCH (cf:CausalFactor {id: 'ECONOMIC_DATA_RELEASE'}), (g:AssetGroup {id: 'INDUSTRIAL_MANUFACTURING'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'UP', r.weight = 0.30, r.confidence = 0.45, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

// PANDEMIC_OUTBREAK: broad negative; pharma benefits; travel/consumer hurt.
MATCH (cf:CausalFactor {id: 'PANDEMIC_OUTBREAK'}), (g:AssetGroup {id: 'PHARMA_INDUSTRY'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'UP', r.weight = 0.55, r.confidence = 0.65, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

MATCH (cf:CausalFactor {id: 'PANDEMIC_OUTBREAK'}), (g:AssetGroup {id: 'AEROSPACE_AVIATION'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'DOWN', r.weight = 0.60, r.confidence = 0.70, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

MATCH (cf:CausalFactor {id: 'PANDEMIC_OUTBREAK'}), (g:AssetGroup {id: 'CONSUMER_FITNESS'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'DOWN', r.weight = 0.50, r.confidence = 0.60, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

MATCH (cf:CausalFactor {id: 'PANDEMIC_OUTBREAK'}), (g:AssetGroup {id: 'PRECIOUS_METALS'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'UP', r.weight = 0.50, r.confidence = 0.60, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

// ENERGY_POLICY: carbon taxes / renewables mandates affect energy-intensive industries.
MATCH (cf:CausalFactor {id: 'ENERGY_POLICY'}), (g:AssetGroup {id: 'OIL_GAS'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'DOWN', r.weight = 0.45, r.confidence = 0.55, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

MATCH (cf:CausalFactor {id: 'ENERGY_POLICY'}), (g:AssetGroup {id: 'AUTOMOTIVE_INDUSTRY'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'UP', r.weight = 0.35, r.confidence = 0.50, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

MATCH (cf:CausalFactor {id: 'ENERGY_POLICY'}), (g:AssetGroup {id: 'INDUSTRIAL_MANUFACTURING'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'DOWN', r.weight = 0.35, r.confidence = 0.50, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

MATCH (cf:CausalFactor {id: 'ENERGY_POLICY'}), (g:AssetGroup {id: 'SEMICONDUCTOR_INDUSTRY'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'UP', r.weight = 0.30, r.confidence = 0.45, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();
