# T07 — Verify E10 Row in `backlog/README.md`

## Context

`backlog/README.md` was already updated during epic creation to include an E10 row in the
Live work table. This task is a verification step only — confirm the row exists and the
link resolves. No content change is expected.

## File to check

**`backlog/README.md`**

Confirm the Live work table contains:

```markdown
| [E10](E10-Cross-Asset-Propagation/README.md) | Cross-asset `CORRELATES_WITH` propagation | M4 | Not started — plan in [E10/README.md](E10-Cross-Asset-Propagation/README.md) |
```

If the row is missing or the link is broken, add or fix it.

## Acceptance criteria

1. E10 row is present in the Live work table.
2. The relative link `E10-Cross-Asset-Propagation/README.md` resolves to an existing file.
3. No other rows in the table are modified.

## Definition of done

- [ ] E10 row confirmed present
- [ ] Link verified to resolve
