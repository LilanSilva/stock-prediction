# T02: Freeze and Label the Conflict Corpus

**Status:** COMPLETE

**Evidence:** [freeze_corpus.py](../../../../src/poc/poc6/freeze_corpus.py), [review decisions](../../../../src/poc/poc6/data/review/review-decisions.json), and [frozen contexts](../../../../src/poc/poc6/data/frozen/conflict-contexts.jsonl). P06 accepted 30 reviewed contexts from 33 candidates before outcomes were joined.

## Purpose

Produce at least 30 reviewed 60-minute contexts containing distinct events with opposing causal forces on `GOLD` or `BRENT_OIL`.

## Requirements

- Use only data frozen by T01.
- Apply local taxonomy mapping and conservative event separation.
- Review event boundaries, canonical event types, affected assets, and whether forces genuinely oppose.
- Require at least two distinct article/event identities per conflict context.
- Freeze context IDs, versions, event IDs, graph-fixture version, and exclusion reasons.
- Do not expose settlement prices or actual directions to reviewers.
- Prefer events after the selected model's knowledge cutoff.

## Acceptance criteria

1. At least 30 contexts pass review, or the task records an explicit insufficient-data decision.
2. Every retained context has complete provenance and a reproducible context hash.
3. Over-merge traps remain separate events.
4. Review occurs before price outcomes are joined.
5. The corpus is immutable once the evaluation begins.
