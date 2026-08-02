// Conditional causal edges (the condition is stored as a property on the CAUSES edge).
//
//   (:CausalFactor)-[:CAUSES {condition, direction, weight, confidence, alpha, beta,
//                             last_updated}]->(:Asset)
//
// Each (factor, asset, condition) is a DISTINCT edge with its own weight/reliability, so different
// factors sharing a condition never collide on weight. The condition is a plain edge property; the
// canonical ConditionCode values live in the shared schema, so no separate node type is needed.
//
// Rationale (docs/decisions): a bare factor->asset edge cannot express context. MILITARY_CONFLICT
// only lifts oil when it threatens transport/supply; a distant conflict is a safe-haven bid that
// lifts gold, not oil. Conditions gate WHICH edge fires; event polarity (OCCURRENCE/RESOLUTION)
// flips the sign at decision time. Beta-Bernoulli priors start at 1.0/1.0 and are refined online by
// Credibility and offline by the structure learner.

// --- Remove the unconditional MILITARY_CONFLICT edges superseded by conditioned ones ---
MATCH (:CausalFactor {id: 'MILITARY_CONFLICT'})-[r:CAUSES]->(:Asset)
WHERE r.condition IS NULL
DELETE r;

// --- MILITARY_CONFLICT under TRANSPORT_AFFECTED: conflict that threatens oil transport/supply ---
MATCH (cf:CausalFactor {id: 'MILITARY_CONFLICT'}), (a:Asset {id: 'BRENT_OIL'})
MERGE (cf)-[r:CAUSES {condition: 'TRANSPORT_AFFECTED'}]->(a)
SET r.direction = 'UP', r.weight = 0.65, r.confidence = 0.75, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

MATCH (cf:CausalFactor {id: 'MILITARY_CONFLICT'}), (a:Asset {id: 'GOLD'})
MERGE (cf)-[r:CAUSES {condition: 'TRANSPORT_AFFECTED'}]->(a)
SET r.direction = 'UP', r.weight = 0.75, r.confidence = 0.80, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

// --- MILITARY_CONFLICT under SAFE_HAVEN_ONLY: distant conflict, no oil-transport impact ---
// Safe-haven bid lifts gold; oil is unaffected (no oil edge on purpose).
MATCH (cf:CausalFactor {id: 'MILITARY_CONFLICT'}), (a:Asset {id: 'GOLD'})
MERGE (cf)-[r:CAUSES {condition: 'SAFE_HAVEN_ONLY'}]->(a)
SET r.direction = 'UP', r.weight = 0.70, r.confidence = 0.75, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();
