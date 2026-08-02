# Canonical Event Taxonomy

Version: `1.0`.

The Cleansing Service maps local NLP output to one of these event types before publishing. Neo4j seed nodes and Prediction matching use exactly the same values.

| Event type | Example actions |
|---|---|
| `MILITARY_CONFLICT` | attack, invade, strike, bombard |
| `STRAIT_CLOSURE` | close, block, restrict passage |
| `SUPPLY_DISRUPTION` | halt production, damage pipeline, cut output |
| `SANCTIONS` | impose sanctions, lift sanctions |
| `RATE_DECISION` | raise rates, cut rates, hold rates |
| `INFLATION_CHANGE` | inflation rises, inflation falls |
| `RECESSION_SIGNAL` | recession warning, contraction signal |
| `CORPORATE_EARNINGS` | report earnings, revise guidance |
| `POLITICAL_TRANSITION` | election result, resignation, appointment |
| `NATURAL_DISASTER` | earthquake, flood, wildfire, hurricane |
| `OTHER` | Valid event not yet represented |

## Event polarity and conditions

Each published event also carries a `polarity` and optional `context_tags` (see the message
contract). `polarity` is `OCCURRENCE` (factor onset, default) or `RESOLUTION` (de-escalation/
negation), which inverts the factor's causal edge sign at decision time. `context_tags` are
`ConditionCode` values (`TRANSPORT_AFFECTED`, `SAFE_HAVEN_ONLY`, `RISK_PREMIUM_ELEVATED`) that gate
which conditioned causal edge fires.

## Multilingual normalization

- spaCy extracts actor/action/object in the article language.
- A local version-controlled mapping normalizes Swedish and English lemmas to taxonomy values.
- BGE-m3 similarity remains Gate 1.
- Taxonomy compatibility is Gate 2.
- The LLM is not used solely to translate action verbs.
- Unmapped actions become `OTHER` with the original lemma retained for later taxonomy review.

