// Feed Analyzer causal knowledge graph — constraints and indexes.
//
// Schema (docs/reference/event-taxonomy.md, docs/reference/asset-registry.md):
//   (:CausalFactor {id})-[:CAUSES {direction, weight, confidence, alpha, beta, last_updated}]->(:Asset {id})
//   - Asset.id is a CANONICAL asset id (GOLD, BRENT_OIL). Provider symbols (e.g. XAUUSD, UKOIL) live
//     only in Market Data adapters, never on graph nodes.
//   - CausalFactor.id is a canonical EventType taxonomy value (MILITARY_CONFLICT, RATE_DECISION, ...).
//   - weight is magnitude in [0,1]; direction is a separate field. alpha/beta start at 1.0/1.0.

CREATE CONSTRAINT asset_id_unique IF NOT EXISTS
  FOR (a:Asset) REQUIRE a.id IS UNIQUE;

CREATE CONSTRAINT causal_factor_id_unique IF NOT EXISTS
  FOR (c:CausalFactor) REQUIRE c.id IS UNIQUE;

CREATE INDEX asset_class_index IF NOT EXISTS
  FOR (a:Asset) ON (a.asset_class);

CREATE INDEX causal_factor_category_index IF NOT EXISTS
  FOR (c:CausalFactor) ON (c.category);
