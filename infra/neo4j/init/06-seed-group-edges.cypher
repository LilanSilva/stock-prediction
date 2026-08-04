// Industry-level CAUSES edges: expert priors attached to (:AssetGroup) rather than a single asset.
//
//   (:CausalFactor)-[:CAUSES {condition, direction, weight, confidence, alpha, beta,
//                             last_updated}]->(:AssetGroup)
//
// WHY THIS FILE EXISTS
// Only GOLD and BRENT_OIL carried causal edges, so every company listing resolved correctly through
// scope inference, pricing and the session calendar, and then produced NO prediction: the decision
// policy needs at least one firing edge. Attaching a prior to the group means all its members inherit
// it (an asset's own edge always overrides), so one edge here gives every listing in that industry
// something to reason with from day one.
//
// SEMANTICS
//   - weight is MAGNITUDE in [0,1]; the sign lives in `direction`, never in the weight.
//   - alpha/beta start at 1.0/1.0 (uninformed Beta prior), refined online by Credibility per scored
//     prediction and offline by the structure learner.
//   - Weights are deliberately LOWER than the commodity edges. A macro factor moves a whole industry
//     less reliably than it moves gold or oil, and these are expert guesses with no evidence behind
//     them yet; starting modest lets real outcomes pull them up rather than having to walk them down.
//   - Conditioned group edges are used only where the condition genuinely changes the direction; most
//     industry priors are unconditional and always fire.
//
// MERGE keeps this idempotent so the seed container can re-run safely.

// --- Defence: conflict and sanctions lift weapons makers (order books, budget expectations) --------
MATCH (cf:CausalFactor {id: 'MILITARY_CONFLICT'}), (g:AssetGroup {id: 'WEAPON_INDUSTRY'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'UP', r.weight = 0.60, r.confidence = 0.65, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

MATCH (cf:CausalFactor {id: 'SANCTIONS'}), (g:AssetGroup {id: 'WEAPON_INDUSTRY'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'UP', r.weight = 0.40, r.confidence = 0.55, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

MATCH (cf:CausalFactor {id: 'POLITICAL_TRANSITION'}), (g:AssetGroup {id: 'WEAPON_INDUSTRY'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'UP', r.weight = 0.30, r.confidence = 0.45, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

// --- Aviation: conflict and fuel costs weigh on airlines; oil supply shocks hurt them -------------
MATCH (cf:CausalFactor {id: 'MILITARY_CONFLICT'}), (g:AssetGroup {id: 'AEROSPACE_AVIATION'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'DOWN', r.weight = 0.40, r.confidence = 0.55, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

MATCH (cf:CausalFactor {id: 'SUPPLY_DISRUPTION'}), (g:AssetGroup {id: 'AEROSPACE_AVIATION'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'DOWN', r.weight = 0.35, r.confidence = 0.50, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

// --- Oil & gas: producers and refiners track the crude complex ------------------------------------
MATCH (cf:CausalFactor {id: 'SUPPLY_DISRUPTION'}), (g:AssetGroup {id: 'OIL_GAS'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'UP', r.weight = 0.55, r.confidence = 0.65, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

MATCH (cf:CausalFactor {id: 'STRAIT_CLOSURE'}), (g:AssetGroup {id: 'OIL_GAS'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'UP', r.weight = 0.55, r.confidence = 0.65, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

// A conflict lifts oil producers only when it actually threatens transport/supply; a distant conflict
// is a safe-haven story with no production impact, so there is no SAFE_HAVEN_ONLY edge here.
MATCH (cf:CausalFactor {id: 'MILITARY_CONFLICT'}), (g:AssetGroup {id: 'OIL_GAS'})
MERGE (cf)-[r:CAUSES {condition: 'TRANSPORT_AFFECTED'}]->(g)
SET r.direction = 'UP', r.weight = 0.50, r.confidence = 0.60, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

MATCH (cf:CausalFactor {id: 'RECESSION_SIGNAL'}), (g:AssetGroup {id: 'OIL_GAS'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'DOWN', r.weight = 0.45, r.confidence = 0.60, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

// --- Precious metals: miners lever the metal price -------------------------------------------------
MATCH (cf:CausalFactor {id: 'MILITARY_CONFLICT'}), (g:AssetGroup {id: 'PRECIOUS_METALS'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'UP', r.weight = 0.50, r.confidence = 0.60, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

MATCH (cf:CausalFactor {id: 'INFLATION_CHANGE'}), (g:AssetGroup {id: 'PRECIOUS_METALS'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'UP', r.weight = 0.45, r.confidence = 0.55, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

// --- Semiconductors: supply chains and sanctions dominate -----------------------------------------
MATCH (cf:CausalFactor {id: 'SUPPLY_DISRUPTION'}), (g:AssetGroup {id: 'SEMICONDUCTOR_INDUSTRY'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'DOWN', r.weight = 0.55, r.confidence = 0.65, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

MATCH (cf:CausalFactor {id: 'SANCTIONS'}), (g:AssetGroup {id: 'SEMICONDUCTOR_INDUSTRY'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'DOWN', r.weight = 0.50, r.confidence = 0.60, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

// --- Rate-sensitive sectors: higher rates compress valuations and demand --------------------------
MATCH (cf:CausalFactor {id: 'RATE_DECISION'}), (g:AssetGroup {id: 'BANKING_FINANCE'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'UP', r.weight = 0.45, r.confidence = 0.60, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

MATCH (cf:CausalFactor {id: 'RATE_DECISION'}), (g:AssetGroup {id: 'REAL_ESTATE'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'DOWN', r.weight = 0.50, r.confidence = 0.60, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

MATCH (cf:CausalFactor {id: 'RATE_DECISION'}), (g:AssetGroup {id: 'SOFTWARE_INDUSTRY'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'DOWN', r.weight = 0.35, r.confidence = 0.50, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

MATCH (cf:CausalFactor {id: 'RATE_DECISION'}), (g:AssetGroup {id: 'INTERNET_PLATFORMS'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'DOWN', r.weight = 0.35, r.confidence = 0.50, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

// --- Cyclicals: recession signals hit discretionary demand and industrial orders ------------------
MATCH (cf:CausalFactor {id: 'RECESSION_SIGNAL'}), (g:AssetGroup {id: 'AUTOMOTIVE_INDUSTRY'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'DOWN', r.weight = 0.50, r.confidence = 0.60, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

MATCH (cf:CausalFactor {id: 'RECESSION_SIGNAL'}), (g:AssetGroup {id: 'INDUSTRIAL_MANUFACTURING'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'DOWN', r.weight = 0.50, r.confidence = 0.60, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

MATCH (cf:CausalFactor {id: 'RECESSION_SIGNAL'}), (g:AssetGroup {id: 'CONSUMER_ELECTRONICS'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'DOWN', r.weight = 0.45, r.confidence = 0.55, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

MATCH (cf:CausalFactor {id: 'RECESSION_SIGNAL'}), (g:AssetGroup {id: 'CONSUMER_FITNESS'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'DOWN', r.weight = 0.45, r.confidence = 0.50, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

MATCH (cf:CausalFactor {id: 'SUPPLY_DISRUPTION'}), (g:AssetGroup {id: 'AUTOMOTIVE_INDUSTRY'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'DOWN', r.weight = 0.40, r.confidence = 0.55, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

MATCH (cf:CausalFactor {id: 'SUPPLY_DISRUPTION'}), (g:AssetGroup {id: 'INDUSTRIAL_MANUFACTURING'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'DOWN', r.weight = 0.40, r.confidence = 0.55, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

// --- Defensives: pharma and staples are inflation/recession resilient rather than cyclical --------
MATCH (cf:CausalFactor {id: 'RECESSION_SIGNAL'}), (g:AssetGroup {id: 'PHARMA_INDUSTRY'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'UP', r.weight = 0.30, r.confidence = 0.45, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

MATCH (cf:CausalFactor {id: 'NATURAL_DISASTER'}), (g:AssetGroup {id: 'PHARMA_INDUSTRY'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'UP', r.weight = 0.35, r.confidence = 0.45, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

MATCH (cf:CausalFactor {id: 'INFLATION_CHANGE'}), (g:AssetGroup {id: 'FOOD_INGREDIENTS'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'DOWN', r.weight = 0.35, r.confidence = 0.50, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

MATCH (cf:CausalFactor {id: 'SUPPLY_DISRUPTION'}), (g:AssetGroup {id: 'FOOD_INGREDIENTS'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'DOWN', r.weight = 0.40, r.confidence = 0.55, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

// --- Telecom: rate-sensitive, defensive under recession -------------------------------------------
MATCH (cf:CausalFactor {id: 'RATE_DECISION'}), (g:AssetGroup {id: 'TELECOM'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'DOWN', r.weight = 0.35, r.confidence = 0.50, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

MATCH (cf:CausalFactor {id: 'SANCTIONS'}), (g:AssetGroup {id: 'TELECOM'})
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'DOWN', r.weight = 0.30, r.confidence = 0.45, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

// --- CORPORATE_EARNINGS: the company-specific factor ----------------------------------------------
// Company news is the case the multi-market feature exists for ("Tesla acquired", "Saab wins order"),
// and CORPORATE_EARNINGS previously had no edge at all -- so every such headline produced nothing.
// Direction is deliberately modest and positive: the polarity/negation cues at extraction time flip
// the sign for bad news, so a single UP prior serves both. The offline learner splits this per company
// once real outcomes accumulate.
MATCH (cf:CausalFactor {id: 'CORPORATE_EARNINGS'}), (g:AssetGroup)
MERGE (cf)-[r:CAUSES]->(g)
SET r.direction = 'UP', r.weight = 0.35, r.confidence = 0.45, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();
