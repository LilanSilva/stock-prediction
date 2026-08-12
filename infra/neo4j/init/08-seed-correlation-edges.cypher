// Cross-asset correlation edges: (:Asset)-[:CORRELATES_WITH {condition, direction, weight,
// confidence, alpha, beta, last_updated}]->(:Asset)
//
// These express second-order price causation: a directional move in one asset that reliably
// causes a directional move in another (e.g. oil UP -> gold DOWN via capital rotation).
//
// Contract (mirrors CAUSES edges — SyRS §9.2):
//   condition   — ConditionCode that must be active for this edge to fire (UPSTREAM_UP or
//                 UPSTREAM_DOWN; see shared/schemas/messages.py ConditionCode).
//   direction   — expected direction on the TARGET asset (UP | DOWN).
//   weight      — expert-assigned magnitude in [0,1]; does NOT carry sign (sign is direction).
//   alpha/beta  — Beta-Bernoulli counts seeded at 1.0/1.0; refined by Credibility Service.
//   last_updated — managed by Credibility; initial value set here.
//
// Rationale (ADR-008): capital rotation — when oil rises, investors move money out of gold
// (both seen as inflation hedges, but oil is the active vehicle during a supply shock).
// The condition UPSTREAM_UP gates the edge: it fires only when the upstream asset was
// predicted UP in the same pipeline run.
//
// MERGE keeps this idempotent.

// One (source, target, condition) triple -> one CORRELATES_WITH edge. This invariant is enforced
// by the MERGE pattern below, which includes `condition` in the matched relationship properties.
//
// No DB-level constraint backs it: a relationship property existence constraint
// (`REQUIRE r.condition IS NOT NULL`) requires Neo4j Enterprise Edition, and this stack runs
// neo4j:5.20-community. Attempting it aborts the seed. Application code never writes a
// CORRELATES_WITH edge without a condition (see CausalGraphClient.upsert_correlation_edge).

// Lookup index for the propagation query: given a source Asset.id + condition, find targets fast.
CREATE INDEX correlates_with_source IF NOT EXISTS
FOR ()-[r:CORRELATES_WITH]-()
ON (r.condition);

// --- XOM_NYSE (oil proxy) UP -> NEM_NYSE (gold proxy) DOWN ---
MATCH (a1:Asset {id: 'XOM_NYSE'}), (a2:Asset {id: 'NEM_NYSE'})
MERGE (a1)-[r:CORRELATES_WITH {condition: 'UPSTREAM_UP'}]->(a2)
SET r.direction     = 'DOWN',
    r.weight        = 0.45,
    r.confidence    = 0.60,
    r.alpha         = 1.0,
    r.beta          = 1.0,
    r.last_updated  = datetime();

// --- XOM_NYSE UP -> LUG_STO (Lundin Gold, precious metals group) DOWN ---
// Lundin Gold is a gold producer; oil-driven capital rotation depresses gold miners as well.
MATCH (a1:Asset {id: 'XOM_NYSE'}), (a2:Asset {id: 'LUG_STO'})
MERGE (a1)-[r:CORRELATES_WITH {condition: 'UPSTREAM_UP'}]->(a2)
SET r.direction     = 'DOWN',
    r.weight        = 0.35,
    r.confidence    = 0.55,
    r.alpha         = 1.0,
    r.beta          = 1.0,
    r.last_updated  = datetime();

// --- Convergence: NEM_NYSE DOWN and LUG_STO DOWN both -> SWED_A_STO ---
// Two upstream gold names reaching one bank at the same propagation depth. This is the case that
// exercises force summation (ADR-008): both edges are collected before SWED_A_STO is decided, so
// the net of the two forces wins rather than whichever edge happened to be traversed first.
// Opposing directions on purpose — a gold selloff is read as risk-on (bank UP) by the heavier
// edge, while the miner-specific lending exposure pulls the other way.
MATCH (a1:Asset {id: 'NEM_NYSE'}), (a2:Asset {id: 'SWED_A_STO'})
MERGE (a1)-[r:CORRELATES_WITH {condition: 'UPSTREAM_DOWN'}]->(a2)
SET r.direction     = 'UP',
    r.weight        = 0.40,
    r.confidence    = 0.45,
    r.alpha         = 1.0,
    r.beta          = 1.0,
    r.last_updated  = datetime();

MATCH (a1:Asset {id: 'LUG_STO'}), (a2:Asset {id: 'SWED_A_STO'})
MERGE (a1)-[r:CORRELATES_WITH {condition: 'UPSTREAM_DOWN'}]->(a2)
SET r.direction     = 'DOWN',
    r.weight        = 0.15,
    r.confidence    = 0.40,
    r.alpha         = 1.0,
    r.beta          = 1.0,
    r.last_updated  = datetime();

// --- NEM_NYSE (gold proxy) UP -> XOM_NYSE (oil proxy) DOWN ---
// Inverse rotation: a safe-haven gold bid (e.g. SAFE_HAVEN_ONLY conflict) can pull money
// from oil-sector equities. Weaker signal — only seed it when upstream gold is predicted UP.
MATCH (a1:Asset {id: 'NEM_NYSE'}), (a2:Asset {id: 'XOM_NYSE'})
MERGE (a1)-[r:CORRELATES_WITH {condition: 'UPSTREAM_UP'}]->(a2)
SET r.direction     = 'DOWN',
    r.weight        = 0.30,
    r.confidence    = 0.50,
    r.alpha         = 1.0,
    r.beta          = 1.0,
    r.last_updated  = datetime();
