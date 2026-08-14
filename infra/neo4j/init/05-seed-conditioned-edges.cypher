// Conditional causal edges (the condition is stored as a property on the CAUSES edge).
//
//   (:CausalFactor)-[:CAUSES {condition, direction, weight, confidence, alpha, beta,
//                             last_updated}]->(:AssetGroup)
//
// Each (factor, target, condition) is a DISTINCT edge with its own weight/reliability, so different
// factors sharing a condition never collide on weight. The condition is a plain edge property; the
// canonical ConditionCode values live in the shared schema, so no separate node type is needed.
//
// Rationale (requirements/ADR-decisions.md ADR-006): a bare factor->asset edge cannot express context.
// MILITARY_CONFLICT only lifts oil when it threatens transport/supply; a distant conflict is a
// safe-haven bid that lifts gold, not oil. Conditions gate WHICH edge fires; event polarity
// (OCCURRENCE/RESOLUTION) flips the sign at decision time. Beta-Bernoulli priors start at 1.0/1.0 and
// are refined online by Credibility and offline by the structure learner.
//
// TARGETS ARE ASSET GROUPS, NOT COMMODITY ASSETS (E12 S02)
// ------------------------------------------------------
// Every statement in this file used to target `(:Asset {id: 'GOLD'})` and `(:Asset {id: 'BRENT_OIL'})`.
// Those nodes are never created — 02-seed-assets.cypher does not define them, because the registry
// dropped the commodity instruments in favour of equity proxies (see the comment above
// EVENT_TYPE_ASSETS in cleansing/taxonomy.py). Cypher's MATCH...MERGE is a silent no-op when the MATCH
// binds nothing and cypher-shell still exits 0, so every expert prior in this file was discarded at
// seed time with no error, for months. 09-verify-seed.cypher now fails the seed if that recurs.
//
// The equivalent targets are the groups the equity proxies belong to: PRECIOUS_METALS (Newmont, Lundin
// Gold) and OIL_GAS (Exxon, IPC). Conditioned GROUP edges are fully supported — both the read query
// (_GROUP_FIRING_EDGES_CYPHER) and the learner's write path (_UPDATE_CONDITIONED_GROUP_EDGE_CYPHER)
// filter on `condition` — so no client change is needed.
//
// DELETE BEFORE MERGE
// -------------------
// A conditioned edge does not replace an unconditional one: an unconditional edge always fires, so
// both would fire and one factor would be counted twice. Each conditioned edge below therefore deletes
// the unconditional edge it supersedes first. MERGE keeps the file idempotent; the DELETE is guarded so
// a re-run is a no-op.

// ONE CONDITIONED EDGE PER (FACTOR, TARGET), NOT ONE PER CONDITION
// ----------------------------------------------------------------
// Condition tags are unioned across the events in a context (`_conditions_by_type` in
// prediction/pipeline.py), so a context holding one transport-affected conflict and one distant
// conflict has BOTH tags active. If a target carried a SAFE_HAVEN_ONLY *and* a TRANSPORT_AFFECTED
// edge, both would then fire and the factor would be counted twice — the same double-count this file
// is otherwise here to prevent.
//
// So a target gets a conditioned edge only where the condition genuinely changes the OUTCOME, which
// means only where one of the conditions implies NO edge at all. Oil qualifies: a distant conflict
// leaves it untouched. Gold does not — it rises on a conflict either way, which is why its prior
// stays unconditional in 06. Conditioning gold would be a distinction without a difference, and it
// would open the double-count path above.

// --- Remove the unconditional MILITARY_CONFLICT edge on OIL_GAS, superseded by the conditioned one -
// Scoped to OIL_GAS alone. Every other group keeps its unconditional MILITARY_CONFLICT prior from 06:
// a war moves defence order books and airline fuel costs regardless of oil logistics, and gold's
// safe-haven bid does not depend on the condition either.
MATCH (:CausalFactor {id: 'MILITARY_CONFLICT'})-[r:CAUSES]->(g:AssetGroup {id: 'OIL_GAS'})
WHERE r.condition IS NULL
DELETE r;

// Any leftover edges to the commodity nodes this file used to target. Defensive: they cannot exist
// while 02 does not create those nodes, but a hand-run of an older seed would have created them.
MATCH (:CausalFactor)-[r:CAUSES]->(a:Asset)
WHERE a.id IN ['GOLD', 'BRENT_OIL']
DELETE r;

// --- MILITARY_CONFLICT under TRANSPORT_AFFECTED: conflict that threatens oil transport/supply ---
// Both metals and oil react. This file is the ONLY place either edge is declared: an unconditional
// MERGE in 06 would bind one of these conditioned edges and overwrite it (see 06's header).
MATCH (cf:CausalFactor {id: 'MILITARY_CONFLICT'}), (g:AssetGroup {id: 'OIL_GAS'})
MERGE (cf)-[r:CAUSES {condition: 'TRANSPORT_AFFECTED'}]->(g)
SET r.direction = 'UP', r.weight = 0.50, r.confidence = 0.60, r.alpha = 1.0, r.beta = 1.0, r.last_updated = datetime();

// --- MILITARY_CONFLICT under SAFE_HAVEN_ONLY: distant conflict, no oil-transport impact ---
// There is deliberately NO edge here. The absence IS the rule: a conflict that does not threaten oil
// logistics must not move the oil proxy, and the metals' unconditional prior in 06 already covers the
// safe-haven bid. This is the whole of ADR-006's condition gating as it applies to the seed.
