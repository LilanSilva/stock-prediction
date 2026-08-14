// Post-seed structural assertions. Runs last (the seed loop globs /seed/*.cypher in name order).
//
// WHY THIS FILE EXISTS
// --------------------
// Cypher's `MATCH ... MERGE` is a silent no-op when the MATCH binds nothing, and `cypher-shell` exits 0
// either way. 04-seed-causal-edges.cypher targeted `(:Asset {id: 'GOLD'})` and `BRENT_OIL`, which
// 02-seed-assets.cypher never creates, so an entire file of expert priors was discarded at seed time
// with no error anywhere — for as long as the file existed. Nothing in the stack noticed: the seed
// container reported success, the services started, and predictions were made from the remaining
// coarser priors.
//
// Each assertion below raises through `apoc.util.validate`, which aborts the transaction and makes
// cypher-shell exit non-zero, so `set -e` in the seed container's entrypoint fails the whole seed.
// APOC is already enabled (NEO4J_PLUGINS in infra/docker-compose.yml).
//
// To exercise a failure deliberately, add a statement targeting a non-existent node id to any earlier
// seed file and re-run the seed container; see infra/README.md.

// --- 1. Every causal factor that Cleansing can emit must be able to fire something ---------------
// A factor with no outgoing CAUSES edge produces contexts that can never decide, which looks exactly
// like "the market did not react" and is impossible to distinguish from a real quiet period.
MATCH (cf:CausalFactor)
WHERE NOT EXISTS { MATCH (cf)-[:CAUSES]->() }
WITH collect(cf.id) AS orphans
CALL apoc.util.validate(
  size(orphans) > 0,
  'seed check 1: causal factors with no CAUSES edge: %s',
  [orphans]
)
RETURN 'check 1 ok' AS check;

// --- 2. No CAUSES or CORRELATES_WITH edge may be missing its decision properties ------------------
// `decide()` multiplies weight by reliability (alpha/beta) and reads `direction` for the sign; a null
// in any of them either crashes the decision or silently drops the edge.
MATCH ()-[r:CAUSES|CORRELATES_WITH]->()
WHERE r.direction IS NULL OR r.weight IS NULL OR r.alpha IS NULL OR r.beta IS NULL
WITH count(r) AS broken
CALL apoc.util.validate(
  broken > 0,
  'seed check 2: %d edge(s) missing direction/weight/alpha/beta',
  [broken]
)
RETURN 'check 2 ok' AS check;

// --- 3. A factor must not carry both a conditioned and an unconditional edge to one target --------
// An unconditional edge always fires, so pairing it with a conditioned edge on the same target counts
// one causal factor twice: it inflates the confidence mass and skews the magnitude average. This is the
// invariant 05's DELETE-before-MERGE maintains.
MATCH (cf:CausalFactor)-[uncond:CAUSES]->(t)
WHERE uncond.condition IS NULL
  AND EXISTS {
    MATCH (cf)-[cond:CAUSES]->(t)
    WHERE cond.condition IS NOT NULL
  }
WITH collect(DISTINCT cf.id + ' -> ' + t.id) AS clashes
CALL apoc.util.validate(
  size(clashes) > 0,
  'seed check 3: factor/target pairs with both conditioned and unconditional edges: %s',
  [clashes]
)
RETURN 'check 3 ok' AS check;

// --- 4. The commodity ids that 04 used to target must not reappear --------------------------------
// They are not registry assets, so Market Data cannot price them and Verification cannot score them: a
// prediction on one would never resolve.
MATCH (n)
WHERE n.id IN ['GOLD', 'BRENT_OIL']
WITH collect(labels(n)[0] + ' ' + n.id) AS revived
CALL apoc.util.validate(
  size(revived) > 0,
  'seed check 4: retired commodity nodes are present again: %s',
  [revived]
)
RETURN 'check 4 ok' AS check;

// --- 5. Every asset must belong to exactly one group ----------------------------------------------
// Group inheritance is how a listing with no learned edges of its own predicts at all; an asset in no
// group silently predicts nothing, and one in two groups double-counts every inherited factor.
MATCH (a:Asset)
WITH a, size([(a)-[:MEMBER_OF]->(:AssetGroup) | 1]) AS groups
WHERE groups <> 1
WITH collect(a.id + ' in ' + toString(groups) + ' group(s)') AS misgrouped
CALL apoc.util.validate(
  size(misgrouped) > 0,
  'seed check 5: assets not in exactly one group: %s',
  [misgrouped]
)
RETURN 'check 5 ok' AS check;

// --- 6. Every CORRELATES_WITH edge must carry a condition ----------------------------------------
// Unlike CAUSES there is no unconditional form: propagation looks edges up BY condition
// (UPSTREAM_UP/UPSTREAM_DOWN), so a null condition makes the edge unreachable rather than always-on.
// A relationship property existence constraint would enforce this, but that needs Neo4j Enterprise and
// this stack runs community, so it is asserted here instead.
MATCH ()-[r:CORRELATES_WITH]->()
WHERE r.condition IS NULL
WITH count(r) AS unconditioned
CALL apoc.util.validate(
  unconditioned > 0,
  'seed check 6: %d CORRELATES_WITH edge(s) with no condition',
  [unconditioned]
)
RETURN 'check 6 ok' AS check;
