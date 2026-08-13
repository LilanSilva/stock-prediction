# REF-01: Canonical Event Taxonomy

## 1. Document control

| | |
|---|---|
| Document ID | `REF-01` |
| Type | Reference data |
| Taxonomy version | `1.3` |
| Status | `Implemented` |
| Version | `1.0.0` |
| Executable source | [`EventType` in src/shared/shared/schemas/messages.py](../src/shared/shared/schemas/messages.py) |
| Consumed by | [SRS-01 §8.2](SRS-01-shared-foundation.md#82-shared-enums), [SRS-03](SRS-03-cleansing.md), [SRS-04](SRS-04-prediction.md) |
| Last verified against code | `2026-08-12` |

This document is **reference data, not a requirement specification.** It contains no `shall`
statements. The requirement that constrains it is
[`SYS-11`](SyRS-system.md#52-identity-and-reference-data): the system accepts only event types
declared here.

The `EventType` enum in [messages.py](../src/shared/shared/schemas/messages.py) is the executable
source of truth. This table must match it value for value.

## 2. Event types

The Cleansing Service maps local NLP output to exactly one of these types before publishing an
`EventDetected`. Neo4j causal-factor seed nodes
([03-seed-causal-factors.cypher](../infra/neo4j/init/03-seed-causal-factors.cypher)) and Prediction's
graph matching use the same 35 values.

Types 1-31 are causal: each has a seeded causal edge and can move an asset. Type 32 (`OTHER`) is a
causal event that is not yet modelled. Types 33-35 are **non-financial** and deliberately have no
causal edge and no asset mapping — see section 2.1.

| # | Event type | Example actions |
|---|---|---|
| 1 | `MILITARY_CONFLICT` | attack, invade, strike, bombard |
| 2 | `STRAIT_CLOSURE` | close, block, restrict passage |
| 3 | `SUPPLY_DISRUPTION` | halt production, damage pipeline, cut output |
| 4 | `SANCTIONS` | impose sanctions, lift sanctions |
| 5 | `RATE_DECISION` | raise rates, cut rates, hold rates |
| 6 | `INFLATION_CHANGE` | inflation rises, inflation falls |
| 7 | `RECESSION_SIGNAL` | recession warning, contraction signal |
| 8 | `CORPORATE_EARNINGS` | report earnings, revise guidance |
| 9 | `POLITICAL_TRANSITION` | election result, resignation, appointment |
| 10 | `NATURAL_DISASTER` | earthquake, flood, wildfire, hurricane |
| 11 | `CORPORATE_ACQUISITION` | merger, acquisition, takeover, buyout, fusion |
| 12 | `EXECUTIVE_CHANGE` | CEO/CFO resigns, steps down, appointed |
| 13 | `REGULATORY_ACTION` | FDA approval/rejection, licence, fine, penalty |
| 14 | `DEBT_CRISIS` | bankruptcy, default, insolvency, credit downgrade |
| 15 | `RESTRUCTURING` | layoffs, job cuts, spin-off, divestiture |
| 16 | `LEGAL_DISPUTE` | lawsuit, class action, fraud investigation, settlement |
| 17 | `PRODUCT_RECALL` | product recalled, safety warning, market ban |
| 18 | `DIVIDEND_CHANGE` | dividend cut, raised, suspended, special dividend |
| 19 | `CONTRACT_WIN` | supply deal, partnership, agreement signed |
| 20 | `SHARE_BUYBACK` | buyback programme, share repurchase |
| 21 | `IPO_LISTING` | IPO, stock market debut, listing |
| 22 | `CYBERSECURITY_INCIDENT` | data breach, cyberattack, ransomware |
| 23 | `TRADE_POLICY` | tariffs, trade war, import/export ban |
| 24 | `FISCAL_POLICY` | stimulus, budget, tax cut, tax hike |
| 25 | `CURRENCY_CRISIS` | devaluation, currency collapse, exchange rate shock |
| 26 | `SOVEREIGN_DEBT` | sovereign default, IMF bailout, country credit downgrade |
| 27 | `GEOPOLITICAL_TENSION` | military exercises, missile test, nuclear threat |
| 28 | `COMMODITY_PRICE_SHOCK` | OPEC production cut, oil/grain price surge |
| 29 | `ECONOMIC_DATA_RELEASE` | GDP, jobs report, PMI, unemployment, retail sales |
| 30 | `PANDEMIC_OUTBREAK` | pandemic, epidemic, outbreak, lockdown |
| 31 | `ENERGY_POLICY` | carbon tax, nuclear power decision, renewable energy mandate |
| 32 | `OTHER` | Valid event not yet represented |
| 33 | `SPORT` | Match result, transfer, fixture, league or tournament news |
| 34 | `ENTERTAINMENT` | Film, television, music, awards, celebrity news |
| 35 | `LIFESTYLE` | Horoscopes, recipes, travel, consumer-interest features |

`OTHER` is the escape hatch, not a failure. An unmapped action becomes `OTHER` with the original
lemma retained so the taxonomy can be reviewed later — see
[SRS-03 §7.4](SRS-03-cleansing.md#7-how-it-works). Prediction handles `OTHER` as a distinct case
([SRS-04 §5.5](SRS-04-prediction.md#5-functional-requirements)) because it has no seeded causal edge.

### 2.1 Non-financial types (33-35)

Ingestion applies no keyword pre-filter — it fetches all recent news and relies on Cleansing to
decide relevance ([SRS-02](SRS-02-ingestion.md)), and three of the four RSS sources are general or
tabloid rather than financial. The majority of ingested articles are therefore not market events at
all.

Types 33-35 make that rejection **explicit**. Before they existed, an irrelevant article either fell
to `OTHER` — overloading a value that means "valid event not yet represented" — or, worse, matched a
generic keyword and was typed as a market event. A measured day of output typed 27 sports articles as
asset-bearing events; each resolved to the gold and oil proxies and could produce a prediction and a
notification from a match report.

These types therefore:

- have **no** entry in the causal-factor seed or in Cleansing's event-type asset mapping, so they
  resolve to zero assets and Prediction drops them (`event_no_assets`);
- are **excluded from Gate 2** like `OTHER`, so they never form clusters — a cluster of them could
  never produce a prediction;
- are matched **before** generic taxonomy keywords but **after** a company-keyword match, so a
  headline naming a listed company is never rejected as sport.

Keep the keywords that select these types specific. Broad words such as "game", "season", "transfer"
or "cup" also occur in market copy and would suppress real news.

## 3. Event polarity and conditions

An event type alone does not determine which causal edge fires. Two additional qualifiers do, both
carried on `EventDetected` and both optional with backward-compatible defaults:

| Qualifier | Values | Default | Effect |
|---|---|---|---|
| `polarity` | `OCCURRENCE`, `RESOLUTION` | `OCCURRENCE` | `RESOLUTION` (de-escalation or negation) inverts the sign of the factor's causal edge at decision time |
| `context_tags` | list of `ConditionCode` | `[]` | Gates which conditioned edge fires |

`ConditionCode` values:

| Condition | Meaning | Derived by |
|---|---|---|
| `TRANSPORT_AFFECTED` | The event disrupts physical movement of goods | Cleansing, from article text |
| `SAFE_HAVEN_ONLY` | The event drives safe-haven demand without disrupting supply | Cleansing, from article text |
| `RISK_PREMIUM_ELEVATED` | The asset already carries an elevated risk premium | **Prediction**, at decision time from recent price history via Market Data `GET /prices/recent` |
| `UPSTREAM_UP` | The source asset was predicted `UP` in the current pipeline run — gates a `CORRELATES_WITH` edge on the downstream asset | **Prediction**, set programmatically during the propagation pass; never set by Cleansing |
| `UPSTREAM_DOWN` | The source asset was predicted `DOWN` in the current pipeline run — gates a `CORRELATES_WITH` edge on the downstream asset | **Prediction**, set programmatically during the propagation pass; never set by Cleansing |

`RISK_PREMIUM_ELEVATED` is the exception: it is not a property of the news, so Cleansing never sets
it. Prediction derives it so a de-escalation only predicts a drop when there is a premium to unwind.
See [SRS-04 §7](SRS-04-prediction.md#7-how-it-works).

`UPSTREAM_UP` and `UPSTREAM_DOWN` are the only condition codes that apply to
`CORRELATES_WITH` edges (Asset→Asset). They are never present on `EventDetected.context_tags`
because they are not a property of the news article — they are derived from a prediction
already made in the same pipeline run. Cleansing must not produce these values.

Full edge-firing semantics are in
[SyRS §9.2](SyRS-system.md#92-neo4j-graph-model) and [SRS-04 §7](SRS-04-prediction.md#7-how-it-works).

## 4. Multilingual normalization

Articles arrive in Swedish and English. Normalization to this taxonomy happens **locally**, before
any clustering comparison and without an LLM call:

- spaCy extracts actor, action, and object in the article's own language.
- A local, version-controlled lemma mapping normalizes Swedish and English actions to a taxonomy
  value.
- BGE-m3 embedding similarity is clustering Gate 1.
- Taxonomy compatibility is clustering Gate 2 — both gates must pass to join a cluster.
- The LLM is **never** used solely to translate an action verb.
- An unmapped action becomes `OTHER`, retaining the original lemma.

This is what lets a Swedish and an English report of the same rate decision join one cluster. The
mechanism is specified in [SRS-03 §7](SRS-03-cleansing.md#7-how-it-works); the mapping tables live in
the Cleansing source.

## 5. How to update this document

Adding an event type is a **contract change**: the value crosses service boundaries inside
`EventDetected.event_type` and is matched against Neo4j causal-factor nodes.

1. Add the value to `EventType` in
   [messages.py](../src/shared/shared/schemas/messages.py) — the executable source of truth.
2. Add the row here and bump the taxonomy version in section 1.
3. Add the lemma mapping in Cleansing so the type can actually be produced.
4. Seed a `CausalFactor` node and at least one `CAUSES` edge
   ([07-seed-new-event-type-edges.cypher](../infra/neo4j/init/07-seed-new-event-type-edges.cypher)) —
   **a type with no edge produces no prediction.** Skip this step only for a non-financial type
   (section 2.1), where producing no prediction is the point.
5. Update [SRS-01 §8.2](SRS-01-shared-foundation.md#82-shared-enums) if the value count changed.
6. Add a row to section 6.

Never renumber or reuse a retired value. A removed event type is a major contract version
([`SYS-28`](SyRS-system.md#54-messaging)).

## 6. Change history

| Date | Version | Change | Driver |
|---|---|---|---|
| `2026-08-06` | `1.0.0` | Moved into `requirements/` from `docs/reference/event-taxonomy.md`; added document control, `OTHER`/`RISK_PREMIUM_ELEVATED` cross-references, and update rules | Requirements consolidation |
| `2026-08-13` | `1.3` | Added non-financial types `SPORT`, `ENTERTAINMENT`, `LIFESTYLE` (33-35) and section 2.1. No causal edge or asset mapping by design; excluded from Gate 2 | Audit of one day of Cleansing output found 27 sports articles typed as asset-bearing events, and `OTHER` overloaded as both "unmapped event" and "irrelevant" |
