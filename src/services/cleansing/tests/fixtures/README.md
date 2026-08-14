# tests/fixtures — Frozen audit replay corpus

## What this is

`audit-2026-08-12-articles.json` holds the 73 distinct articles that produced the 113 predictions in
[`scripts/prediction-audit-2026-08-12.json`](../../../../../scripts/prediction-audit-2026-08-12.json).
`audit-2026-08-12-labels.json` holds the expected classification for each one.

Together they are the acceptance oracle for
E12 — Prediction Quality Remediation:
an audit of that day found 93 of 113 predictions were not justified by their contributing news.
[`test_audit_replay.py`](../test_audit_replay.py) replays every article through the real classifier and
asserts the labelled outcome.

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
