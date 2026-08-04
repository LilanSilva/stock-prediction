# Canonical Event Taxonomy

Version: `1.1`.

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
| `CORPORATE_ACQUISITION` | merger, acquisition, takeover, buyout, fusion |
| `EXECUTIVE_CHANGE` | CEO/CFO resigns, steps down, appointed |
| `REGULATORY_ACTION` | FDA approval/rejection, licence, fine, penalty |
| `DEBT_CRISIS` | bankruptcy, default, insolvency, credit downgrade |
| `RESTRUCTURING` | layoffs, job cuts, spin-off, divestiture |
| `LEGAL_DISPUTE` | lawsuit, class action, fraud investigation, settlement |
| `PRODUCT_RECALL` | product recalled, safety warning, market ban |
| `DIVIDEND_CHANGE` | dividend cut, raised, suspended, special dividend |
| `CONTRACT_WIN` | supply deal, partnership, agreement signed |
| `SHARE_BUYBACK` | buyback programme, share repurchase |
| `IPO_LISTING` | IPO, stock market debut, listing |
| `CYBERSECURITY_INCIDENT` | data breach, cyberattack, ransomware |
| `TRADE_POLICY` | tariffs, trade war, import/export ban |
| `FISCAL_POLICY` | stimulus, budget, tax cut, tax hike |
| `CURRENCY_CRISIS` | devaluation, currency collapse, exchange rate shock |
| `SOVEREIGN_DEBT` | sovereign default, IMF bailout, country credit downgrade |
| `GEOPOLITICAL_TENSION` | military exercises, missile test, nuclear threat |
| `COMMODITY_PRICE_SHOCK` | OPEC production cut, oil/grain price surge |
| `ECONOMIC_DATA_RELEASE` | GDP, jobs report, PMI, unemployment, retail sales |
| `PANDEMIC_OUTBREAK` | pandemic, epidemic, outbreak, lockdown |
| `ENERGY_POLICY` | carbon tax, nuclear power decision, renewable energy mandate |
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

