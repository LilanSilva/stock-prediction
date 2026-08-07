# T06 — Update REF-01 (Canonical Event Taxonomy): New Condition Codes

## Context

`requirements/REF-01-event-taxonomy.md` is the reference document for the canonical event
taxonomy and condition codes. Section 3 defines all `ConditionCode` values. E10 adds
`UPSTREAM_UP` and `UPSTREAM_DOWN` — these must be listed here because REF-01 is the single
source of truth for condition code definitions (`SYS-11`).

Read the full file before editing. The section to change is section 3
("Event polarity and conditions").

## Changes required

### 1. Section 3 — `ConditionCode` values table: add two new rows

Find the `ConditionCode` values table:

```markdown
| Condition | Meaning | Derived by |
|---|---|---|
| `TRANSPORT_AFFECTED` | The event disrupts physical movement of goods | Cleansing, from article text |
| `SAFE_HAVEN_ONLY` | The event drives safe-haven demand without disrupting supply | Cleansing, from article text |
| `RISK_PREMIUM_ELEVATED` | The asset already carries an elevated risk premium | **Prediction**, at decision time from recent price history via Market Data `GET /prices/recent` |
```

Add two rows:

```markdown
| `UPSTREAM_UP` | The source asset was predicted `UP` in the current pipeline run — gates a `CORRELATES_WITH` edge on the downstream asset | **Prediction**, set programmatically during the propagation pass; never set by Cleansing |
| `UPSTREAM_DOWN` | The source asset was predicted `DOWN` in the current pipeline run — gates a `CORRELATES_WITH` edge on the downstream asset | **Prediction**, set programmatically during the propagation pass; never set by Cleansing |
```

### 2. Add a note below the table

After the table, add:

```markdown
`UPSTREAM_UP` and `UPSTREAM_DOWN` are the only condition codes that apply to
`CORRELATES_WITH` edges (Asset→Asset). They are never present on `EventDetected.context_tags`
because they are not a property of the news article — they are derived from a prediction
already made in the same pipeline run. Cleansing must not produce these values.
```

### 3. Bump taxonomy version and last-verified date

Change `Taxonomy version` to `1.2` and `Last verified against code` to `2026-08-07`.

## Acceptance criteria

1. `UPSTREAM_UP` and `UPSTREAM_DOWN` rows present in the condition code table.
2. Each row correctly states "Derived by: **Prediction**, propagation pass; never set by Cleansing".
3. Clarifying note added below the table.
4. Taxonomy version bumped to `1.2`.
5. No existing condition code row modified or removed.
6. All relative links still resolve.

## Definition of done

- [ ] Two new `ConditionCode` rows added to section 3 table
- [ ] Clarifying note added
- [ ] Taxonomy version bumped to `1.2`
- [ ] All relative links verified
