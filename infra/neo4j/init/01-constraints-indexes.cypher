// Feed Analyzer causal knowledge graph — constraints and indexes.
//
// Schema (requirements/REF-01-event-taxonomy.md, requirements/REF-02-asset-registry.md):
//   (:CausalFactor {id})-[:CAUSES {direction, weight, confidence, alpha, beta, last_updated}]->(:Asset {id})
//   (:CausalFactor {id})-[:CAUSES {...}]->(:AssetGroup {id})   -- industry-level edge, inherited
//   (:Asset {id})-[:MEMBER_OF]->(:AssetGroup {id})
//   - Asset.id is a CANONICAL asset id (GOLD, SAAB_B_STO). Provider symbols (e.g. XAUUSD,
//     SAAB-B.ST) live only in the registry and Market Data adapters, never on graph nodes.
//   - An asset's own CAUSES edges take precedence; when it has none for a factor, its group's edges
//     are inherited, so a newly listed company predicts before it has company-specific evidence.
//   - CausalFactor.id is a canonical EventType taxonomy value (MILITARY_CONFLICT, RATE_DECISION, ...).
//   - weight is magnitude in [0,1]; direction is a separate field. alpha/beta start at 1.0/1.0.

CREATE CONSTRAINT asset_id_unique IF NOT EXISTS
  FOR (a:Asset) REQUIRE a.id IS UNIQUE;

CREATE CONSTRAINT asset_group_id_unique IF NOT EXISTS
  FOR (g:AssetGroup) REQUIRE g.id IS UNIQUE;

CREATE CONSTRAINT causal_factor_id_unique IF NOT EXISTS
  FOR (c:CausalFactor) REQUIRE c.id IS UNIQUE;

CREATE INDEX asset_class_index IF NOT EXISTS
  FOR (a:Asset) ON (a.asset_class);

CREATE INDEX causal_factor_category_index IF NOT EXISTS
  FOR (c:CausalFactor) ON (c.category);
