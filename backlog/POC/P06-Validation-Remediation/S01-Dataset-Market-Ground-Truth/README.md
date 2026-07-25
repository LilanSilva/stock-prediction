# S01: Dataset and Market Ground Truth

**Status:** COMPLETE

## Goal

Create the frozen inputs needed to evaluate prediction arbitration without future or outcome leakage.

## Tasks

| Task | Deliverable |
|---|---|
| [T01](T01-point-in-time-context-collector.md) | Resumable source collector with bounded retention and provenance |
| [T02](T02-freeze-and-label-conflict-corpus.md) | Hand-reviewed corpus of at least 30 distinct opposing-force contexts |
| [T03](T03-reference-price-calendar-roll-policy.md) | Validated Gold/Brent series, session mapping, fallback decision, and rollover policy |
