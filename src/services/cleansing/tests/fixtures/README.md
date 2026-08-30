# tests/fixtures — Frozen audit replay corpora

Two corpora, from two audits, answering **different questions**. Both are needed; neither subsumes the
other.

| Corpus | Labelled by | Catches |
|---|---|---|
| `audit-2026-08-12-*` (73 articles, `A1`…`A73`) | **asset outcome** | An article that wrongly produces a prediction |
| `audit-2026-08-17-*` (251 articles, `B1`…`B251`) | **event type** | An article given the wrong type at all |

The 2026-08-12 corpus cannot detect a classification regression: 65 of its 73 labels are
`must_not_predict`, and an article typed `OTHER` instead of `SPORT` resolves to no assets either way, so
it passes. The 2026-08-17 corpus exists to close that gap.

## 2026-08-12 — asset outcome

`audit-2026-08-12-articles.json` holds the 73 distinct articles that produced the 113 predictions in
[`scripts/prediction-audit-2026-08-12.json`](../../../../../scripts/prediction-audit-2026-08-12.json).
`audit-2026-08-12-labels.json` holds the expected classification for each one.

Together they are the acceptance oracle for the prediction-quality remediation that produced
CLN-63 – CLN-68 (see [`requirements/SRS-03-cleansing.md`](../../../../../requirements/SRS-03-cleansing.md) §5):
an audit of that day found 93 of 113 predictions were not justified by their contributing news.
[`test_audit_replay.py`](../test_audit_replay.py) replays every article through the real classifier and
asserts the labelled outcome.

## 2026-08-17 — event type

`audit-2026-08-17-articles.json` holds all 251 clusters produced that day, one article each, and
`audit-2026-08-17-labels.json` the reviewed expected type for each. Source:
[`scripts/cleansing-audit-2026-08-17.json`](../../../../../scripts/cleansing-audit-2026-08-17.json),
with the per-cluster verdicts in
[`scripts/cleansing-misclassified-2026-08-17.md`](../../../../../scripts/cleansing-misclassified-2026-08-17.md).
It is the oracle for CLN-69 – CLN-72, replayed by
[`test_audit_replay_2026_08_17.py`](../test_audit_replay_2026_08_17.py).

That audit found **110 of 251 clusters correctly classified**. 222 were typed `OTHER`, including
roughly 85 sport, culture and lifestyle articles that
[REF-01](../../../../../requirements/REF-01-event-taxonomy.md) §2.1 requires be *explicitly* rejected —
the non-financial reject tier fired twice all day, because its keyword vocabulary is English-first and
every feed is Swedish.

### Provenance and two things a reader must know

**Re-exported before freezing.** The original export capped bodies at 500 characters and omitted
`canonical_url`. Both are classification inputs, so that export could not reproduce its own results:
two clusters (`86343317`, `9a05ce28`) took their type from body text past the cut. The corpus is built
from a re-export with the full stored body and the URL, and now reproduces **251 of 251** stored types.
`scripts/export-cleansing-audit.ps1` has been fixed; a fresh export needs no repair.

Bodies are capped at 2 000 characters — that is `body_max_chars` in Ingestion, i.e. what the database
actually holds, not a truncation applied here.

**The corpus is a subset of that day, on purpose.** Ingestion kept running after the audit export, so
the date now holds 279 articles. The corpus is the 251 that were **audited and labelled**; the 28 later
arrivals are excluded because no one reviewed them. All 251 were re-verified as still present with
unchanged stored types before freezing.

### `expected_event_type: null`

19 labels carry `null`, meaning "any non-financial or unmapped type". These are culture-section essays,
columns and human-interest features where `ENTERTAINMENT`, `LIFESTYLE` and `OTHER` are
**downstream-equivalent**: all three resolve to zero assets and all three are excluded from Gate 2, so
Prediction behaves identically. Asserting one of them would entrench a reviewer's opinion without
protecting any behaviour. Precision is still asserted for these articles.

### Two ratchets, not a single pass/fail

`test_audit_replay_2026_08_17.py` holds two sets of refs that are asserted to be **exactly** the set
that currently fails:

| Set | Meaning | Severity |
|---|---|---|
| `_KNOWN_MISCLASSIFIED` | Labelled type not yet produced | Usually resolves to nothing; costs a missed cluster |
| `_KNOWN_ASSET_LEAKS` | Misclassified **and** reaches an asset | Produces a false prediction and a notification |

Because each set must match reality exactly, fixing an article breaks the build until its ref is
removed, and breaking one breaks the build too. Improvements cannot land unrecorded and regressions
cannot hide. `_KNOWN_ASSET_LEAKS` is the serious list: it currently holds three articles — a
reader-service graphic moving two mining stocks, a reader's opinion letter moving Tesla, and a White
House denial moving oil.

## Provenance

Extracted from the prediction audit export, deduplicated by `article_id`, in first-appearance order.
`ref` (`A1`…`A73`) is that order and is the stable handle used in the E12 documents.

**The source export is double-encoded.** `scripts/export-prediction-audit.ps1` captured `psql` output
through PowerShell's OEM console code page, so the export holds `l├ñge` where the database holds
`läge`. The corpus reverses that round trip (`cp437` → `utf-8`), so this fixture is faithful and the
export is not. The export script has since been fixed; a fresh export will not need repair, and
re-extracting from an old export will.

`language` is inferred from Swedish diacritics and stopwords across title and body. It is metadata
only — `classify_text` does not take a language, and only `SpacyExtractor` uses one.

## Labelling rubric

| Field | Meaning |
|---|---|
| `expected_event_type` | The canonical type the article must classify to. `null` means "any non-financial or unmapped type" — the article carries no causal event and the specific type does not matter. |
| `expected_assets` | The exact asset set the article must resolve to. Order-insensitive. `[]` means the article must produce no prediction at all. |
| `verdict` | Why the label is what it is (see below). |
| `rationale` | One sentence naming the mechanism, so a future reader can judge the label rather than trusting it. |

| Verdict | Count | Meaning |
|---|---|---|
| `must_not_predict` | 65 | Non-market article, or a market-typed article whose assets were selected by a defect. Must resolve to no assets. |
| `justified` | 6 | The prediction was sound. These must keep working — precision must not be bought by breaking them. |
| `known_recall_gap` | 1 | A prediction that *should* be produced but is not, for a reason outside E12's scope. Documented, not asserted. |
| `accepted_limitation` | 1 | Current behaviour is questionable but is a registry judgement, not a code defect. Asserted as-is so a change is deliberate. |

## The rule that matters

**Labels are corrected by review, never to make a test pass.** If the classifier disagrees with a
label, the default assumption is that the classifier is wrong. Changing a label requires stating why
in `rationale` — that is the whole reason the field exists.

## Recall is part of the contract

Only 4 of the 7 predictions the audit judged justified are still producible: earlier fixes bought
precision partly by breaking the good cases. `A20` (CoreWeave → NVDA) and the pre-fix behaviour of
`A22` (LBMA gold forecast → gold miners) are recorded here so that loss stays visible.

A change that makes every `must_not_predict` article pass while also breaking a `justified` one has
not improved the system. Read both columns.
