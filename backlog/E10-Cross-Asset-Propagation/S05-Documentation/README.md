# S05 — Documentation Updates

## Overview

Every specification and decision record that describes affected components must be updated
in the same commit as the code changes. Per `copilot-instructions.md`: a requirement with no
proving test is `Approved`, not `Implemented`; a stale document is worse than a missing one.

This story has **no code changes**. It must be completed after S01–S04 and S06 are all done.

## Dependencies

S01–S04 and S06 must all be complete so the documentation reflects the fully implemented
behaviour. In particular:
- SRS-07 must describe both the online (S04) and offline (S06) correlation learning paths.
- SyRS must describe the new `CORRELATES_WITH` edge type and the updated `PredictionMade` contract.
- SRS-01 must list the new shared enums and the updated `PredictionMade` model.

## Full document impact list

Every document that changes is listed below. Read each file before editing it.

| Document | File | What changes | Task |
|---|---|---|---|
| ADR — Architecture Decision Records | `requirements/ADR-decisions.md` | New ADR-008 entry | T01 |
| SyRS — System spec | `requirements/SyRS-system.md` | §3 definitions, §9.2 graph model, §4 end-to-end flow step 3, §8 canonical messages | T02 |
| SRS-01 — Shared foundation | `requirements/SRS-01-shared-foundation.md` | §8.2 shared enums (`ConditionCode` two new values), `SHR-9` requirement, `PredictionMade` fields | T03 |
| SRS-04 — Prediction Service | `requirements/SRS-04-prediction.md` | §2.1 responsibilities, §3 definitions, new `PRD-*` requirements | T04 |
| SRS-07 — Credibility Service | `requirements/SRS-07-credibility.md` | §2 responsibilities (online + offline paths), §3 definitions, new `CRD-*` requirements | T05 |
| REF-01 — Event taxonomy | `requirements/REF-01-event-taxonomy.md` | §3 condition codes — two new values | T06 |
| `backlog/README.md` | `backlog/README.md` | Register E10 in Live work table (already done — verify only) | T07 |

**Documents confirmed NOT changed by E10:**
- `SRS-02-ingestion.md` — Ingestion is not touched.
- `SRS-03-cleansing.md` — Cleansing is not touched; `UPSTREAM_UP`/`UPSTREAM_DOWN` are never set by Cleansing.
- `SRS-05-market-data.md` — Market Data is not touched.
- `SRS-06-verification.md` — Verification scores each `PredictionMade` independently; the existing scoring logic handles propagated predictions without change.
- `SRS-10-notification.md` — Notification is not built; no change.
- `REF-02-asset-registry.md` — Asset registry format and routing are not changed.

## Tasks

| Task | File | Summary |
|---|---|---|
| [T01](T01-adr-008.md) | `requirements/ADR-decisions.md` | Write ADR-008 |
| [T02](T02-syrs-update.md) | `requirements/SyRS-system.md` | Update system spec |
| [T03](T03-srs-01-update.md) | `requirements/SRS-01-shared-foundation.md` | Update shared foundation spec |
| [T04](T04-srs-04-update.md) | `requirements/SRS-04-prediction.md` | Update prediction spec |
| [T05](T05-srs-07-update.md) | `requirements/SRS-07-credibility.md` | Update credibility spec |
| [T06](T06-ref-01-update.md) | `requirements/REF-01-event-taxonomy.md` | Add two new condition codes |
| [T07](T07-backlog-readme-verify.md) | `backlog/README.md` | Verify E10 row is present |

All seven tasks are independent of each other and can run in parallel.

## How to verify completeness

```bash
# No TODO or placeholder text remains in any updated file
grep -r "TODO\|PLACEHOLDER\|TBD" \
  requirements/ADR-decisions.md \
  requirements/SyRS-system.md \
  requirements/SRS-01-shared-foundation.md \
  requirements/SRS-04-prediction.md \
  requirements/SRS-07-credibility.md \
  requirements/REF-01-event-taxonomy.md

# All relative links resolve
grep -rE '\[.*\]\(\.\.?/' requirements/ --include="*.md" \
  | sed 's/.*](\(.*\))/\1/' \
  | while read f; do [ -f "requirements/$f" ] || echo "BROKEN: $f"; done

# Document versions were bumped
grep "Version" \
  requirements/SyRS-system.md \
  requirements/SRS-01-shared-foundation.md \
  requirements/SRS-04-prediction.md \
  requirements/SRS-07-credibility.md \
  requirements/REF-01-event-taxonomy.md
```
