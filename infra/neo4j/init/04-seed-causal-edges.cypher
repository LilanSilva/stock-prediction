// Seed CAUSES edges between canonical factors and the two POC assets (GOLD, BRENT_OIL).
//
// Contract alignment (requirements/SyRS-system.md sec 9.2 edge business key, SRS-04/SRS-07):
//   - weight is MAGNITUDE in [0,1]; sign is carried by the separate `direction` field, not the weight.
//   - Beta-Bernoulli learnable state starts at alpha=1.0, beta=1.0 (NOT 2/2).
//   - direction is UP | DOWN | NEUTRAL.
//   - last_updated lets Credibility track edge modification time.
//
// These edges intentionally create compound/conflicting forces so the graph-only M1 policy has real
// decisions to resolve (e.g. MILITARY_CONFLICT pushes GOLD up while SANCTIONS-driven USD strength is
// a competing macro force handled via future arbitration, deferred by POC-6). MERGE keeps it idempotent.

// --- MILITARY_CONFLICT ---
MATCH (cf:CausalFactor {id: 'MILITARY_CONFLICT'}), (a:Asset {id: 'GOLD'})
MERGE (cf)-[r:CAUSES]->(a)
SET r.direction = 'UP', r.weight = 0.75, r.confidence = 0.80, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

MATCH (cf:CausalFactor {id: 'MILITARY_CONFLICT'}), (a:Asset {id: 'BRENT_OIL'})
MERGE (cf)-[r:CAUSES]->(a)
SET r.direction = 'UP', r.weight = 0.65, r.confidence = 0.75, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

// --- STRAIT_CLOSURE (e.g. Hormuz): strong oil supply-side force ---
MATCH (cf:CausalFactor {id: 'STRAIT_CLOSURE'}), (a:Asset {id: 'BRENT_OIL'})
MERGE (cf)-[r:CAUSES]->(a)
SET r.direction = 'UP', r.weight = 0.80, r.confidence = 0.80, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

MATCH (cf:CausalFactor {id: 'STRAIT_CLOSURE'}), (a:Asset {id: 'GOLD'})
MERGE (cf)-[r:CAUSES]->(a)
SET r.direction = 'UP', r.weight = 0.45, r.confidence = 0.65, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

// --- SUPPLY_DISRUPTION ---
MATCH (cf:CausalFactor {id: 'SUPPLY_DISRUPTION'}), (a:Asset {id: 'BRENT_OIL'})
MERGE (cf)-[r:CAUSES]->(a)
SET r.direction = 'UP', r.weight = 0.85, r.confidence = 0.85, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

// --- SANCTIONS ---
MATCH (cf:CausalFactor {id: 'SANCTIONS'}), (a:Asset {id: 'BRENT_OIL'})
MERGE (cf)-[r:CAUSES]->(a)
SET r.direction = 'UP', r.weight = 0.55, r.confidence = 0.65, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

MATCH (cf:CausalFactor {id: 'SANCTIONS'}), (a:Asset {id: 'GOLD'})
MERGE (cf)-[r:CAUSES]->(a)
SET r.direction = 'UP', r.weight = 0.40, r.confidence = 0.60, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

// --- RATE_DECISION: higher rates lift USD, pressuring gold; softer for oil ---
MATCH (cf:CausalFactor {id: 'RATE_DECISION'}), (a:Asset {id: 'GOLD'})
MERGE (cf)-[r:CAUSES]->(a)
SET r.direction = 'DOWN', r.weight = 0.60, r.confidence = 0.75, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

MATCH (cf:CausalFactor {id: 'RATE_DECISION'}), (a:Asset {id: 'BRENT_OIL'})
MERGE (cf)-[r:CAUSES]->(a)
SET r.direction = 'DOWN', r.weight = 0.40, r.confidence = 0.60, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

// --- INFLATION_CHANGE: inflation supports gold as a hedge ---
MATCH (cf:CausalFactor {id: 'INFLATION_CHANGE'}), (a:Asset {id: 'GOLD'})
MERGE (cf)-[r:CAUSES]->(a)
SET r.direction = 'UP', r.weight = 0.50, r.confidence = 0.65, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

// --- RECESSION_SIGNAL: demand fear weighs on oil; mild safe-haven bid for gold ---
MATCH (cf:CausalFactor {id: 'RECESSION_SIGNAL'}), (a:Asset {id: 'BRENT_OIL'})
MERGE (cf)-[r:CAUSES]->(a)
SET r.direction = 'DOWN', r.weight = 0.60, r.confidence = 0.70, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

MATCH (cf:CausalFactor {id: 'RECESSION_SIGNAL'}), (a:Asset {id: 'GOLD'})
MERGE (cf)-[r:CAUSES]->(a)
SET r.direction = 'UP', r.weight = 0.45, r.confidence = 0.60, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

// --- NATURAL_DISASTER: infrastructure damage can disrupt oil supply ---
MATCH (cf:CausalFactor {id: 'NATURAL_DISASTER'}), (a:Asset {id: 'BRENT_OIL'})
MERGE (cf)-[r:CAUSES]->(a)
SET r.direction = 'UP', r.weight = 0.45, r.confidence = 0.55, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

MATCH (cf:CausalFactor {id: 'NATURAL_DISASTER'}), (a:Asset {id: 'GOLD'})
MERGE (cf)-[r:CAUSES]->(a)
SET r.direction = 'UP', r.weight = 0.25, r.confidence = 0.50, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

// --- POLITICAL_TRANSITION: uncertainty gives gold a modest safe-haven bid ---
MATCH (cf:CausalFactor {id: 'POLITICAL_TRANSITION'}), (a:Asset {id: 'GOLD'})
MERGE (cf)-[r:CAUSES]->(a)
SET r.direction = 'UP', r.weight = 0.35, r.confidence = 0.55, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();
