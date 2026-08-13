#!/usr/bin/env python
"""Measure prediction accuracy, with explicit before/after windows so changes are attributable.

Directional accuracy is the headline number, and it is **not** the same as the raw correct/total
ratio: roughly a quarter of real outcomes are NEUTRAL (the price moved less than verification's
0.3% deadband), so including them understates accuracy. This script reports both.

It also reports the diagnostics that mattered in the 2026-08-13 accuracy investigation, because each
one was a defect that a single accuracy number hid:

  * per-event-type accuracy — one type (COMMODITY_PRICE_SHOCK) was 10% accurate and dragged the
    whole system down while every other type was 60-80%
  * confidence distribution — 292 of 296 predictions reported exactly 1.00, making the number
    useless and Notification's ``min_confidence`` gate inert
  * NEUTRAL emission rate — was 0, so a quarter of outcomes were unwinnable by construction
  * predictions per asset per day — one asset accumulated 52 in a day, flip-flopping UP/DOWN
  * withdrawn share — superseded predictions must not reach the KG learner

Usage:
    python scripts/measure-prediction-accuracy.py
    python scripts/measure-prediction-accuracy.py --since 2026-08-14
    python scripts/measure-prediction-accuracy.py --split 2026-08-13T20:00:00Z

``--split`` prints the same report for the windows either side of a timestamp, which is how a
behaviour change is attributed: compare the window before the deploy with the window after. Use the
deploy time of the change you are measuring.

Reads ``DATABASE_URL`` from the environment (load ``infra/.env`` first).

Exit code is 0 when the report is produced; 1 when the database is unreachable or empty.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime

try:
    import asyncpg
except ImportError:  # pragma: no cover - dependency is in the root requirements
    sys.exit("asyncpg is not installed; run scripts/setup-venv.ps1 (or .sh) first")


_SUMMARY = """
SELECT count(*)                                                        AS predictions,
       count(*) FILTER (WHERE p.status = 'WITHDRAWN')                  AS withdrawn,
       count(*) FILTER (WHERE p.direction = 'NEUTRAL')                 AS neutral_emitted,
       count(vs.prediction_id)                                         AS scored,
       count(*) FILTER (WHERE vs.is_correct)                           AS correct,
       count(vs.prediction_id) FILTER (WHERE vs.actual_direction <> 'NEUTRAL')
                                                                       AS scored_directional,
       count(*) FILTER (WHERE vs.is_correct AND vs.actual_direction <> 'NEUTRAL')
                                                                       AS correct_directional
FROM   prediction.predictions p
LEFT   JOIN verification.scores vs ON vs.prediction_id = p.prediction_id
WHERE  p.decision_at >= $1 AND ($2::timestamptz IS NULL OR p.decision_at < $2)
"""

_BY_EVENT_TYPE = """
SELECT (SELECT ce.event_type
        FROM   prediction.context_events ce
        WHERE  ce.context_id = p.context_id
        LIMIT  1)                                        AS event_type,
       count(*)                                          AS scored,
       count(*) FILTER (WHERE vs.is_correct)             AS correct
FROM   prediction.predictions p
JOIN   verification.scores vs ON vs.prediction_id = p.prediction_id
WHERE  p.decision_at >= $1 AND ($2::timestamptz IS NULL OR p.decision_at < $2)
  AND  vs.actual_direction <> 'NEUTRAL'
GROUP  BY 1
ORDER  BY 2 DESC
"""

_CONFIDENCE = """
SELECT round(p.confidence::numeric, 2) AS confidence,
       count(*)                        AS predictions,
       count(vs.prediction_id)         AS scored,
       count(*) FILTER (WHERE vs.is_correct AND vs.actual_direction <> 'NEUTRAL')
                                       AS correct_directional,
       count(vs.prediction_id) FILTER (WHERE vs.actual_direction <> 'NEUTRAL')
                                       AS scored_directional
FROM   prediction.predictions p
LEFT   JOIN verification.scores vs ON vs.prediction_id = p.prediction_id
WHERE  p.decision_at >= $1 AND ($2::timestamptz IS NULL OR p.decision_at < $2)
GROUP  BY 1
ORDER  BY 1 DESC
"""

# Per-asset-per-day volume, which is what the daily cap constrains. Counted on the UTC date here for
# a simple cross-asset overview; the service itself enforces the cap on each asset's LOCAL date.
_PER_ASSET_PER_DAY = """
SELECT p.decision_at::date          AS day,
       p.asset_id                   AS asset_id,
       count(*)                     AS predictions,
       count(*) FILTER (WHERE p.status <> 'WITHDRAWN') AS active
FROM   prediction.predictions p
WHERE  p.decision_at >= $1 AND ($2::timestamptz IS NULL OR p.decision_at < $2)
GROUP  BY 1, 2
HAVING count(*) FILTER (WHERE p.status <> 'WITHDRAWN') > $3
ORDER  BY 4 DESC, 3 DESC
LIMIT  10
"""

def _parse(value: str) -> datetime:
    """Parse an ISO date/timestamp, defaulting a naive value to UTC (the column is timestamptz)."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _pct(part: int, whole: int) -> str:
    return "n/a" if not whole else f"{100.0 * part / whole:.1f}%"


async def _report(
    pool: asyncpg.Pool, label: str, start: datetime, end: datetime | None, *, cap: int
) -> int:
    row = await pool.fetchrow(_SUMMARY, start, end)
    total = row["predictions"]
    print(f"\n{'=' * 78}\n{label}\n{'=' * 78}")
    if not total:
        print("  no predictions in this window")
        return 0

    print(f"  predictions            {total}")
    print(f"    withdrawn            {row['withdrawn']}  ({_pct(row['withdrawn'], total)})")
    print(
        f"    NEUTRAL emitted      {row['neutral_emitted']}  "
        f"({_pct(row['neutral_emitted'], total)})"
    )
    print(f"  scored                 {row['scored']}")
    print(
        f"    correct (all)        {row['correct']}  "
        f"-> {_pct(row['correct'], row['scored'])}"
    )
    print(
        f"    DIRECTIONAL accuracy {row['correct_directional']}/{row['scored_directional']}"
        f"  -> {_pct(row['correct_directional'], row['scored_directional'])}"
        "   <-- headline"
    )
    unwinnable = row["scored"] - row["scored_directional"]
    print(
        f"    NEUTRAL outcomes     {unwinnable}  ({_pct(unwinnable, row['scored'])} of scored) "
        "- only winnable if the system can emit NEUTRAL"
    )

    by_type = await pool.fetch(_BY_EVENT_TYPE, start, end)
    if by_type:
        print("\n  accuracy by event type (directional outcomes only):")
        for r in by_type:
            name = r["event_type"] or "(unknown)"
            print(
                f"    {name:<24} {r['correct']:>4}/{r['scored']:<4} "
                f"-> {_pct(r['correct'], r['scored'])}"
            )

    confidences = await pool.fetch(_CONFIDENCE, start, end)
    if confidences:
        print("\n  confidence distribution (a single dominant value means it carries no signal):")
        for r in confidences[:12]:
            acc = _pct(r["correct_directional"], r["scored_directional"])
            print(
                f"    {float(r['confidence']):<6} {r['predictions']:>5} predictions, "
                f"{r['scored']:>4} scored, directional {acc}"
            )

    over_cap = await pool.fetch(_PER_ASSET_PER_DAY, start, end, cap)
    print(f"\n  asset-days with more than {cap} active predictions (the daily cap):")
    if not over_cap:
        print("    none - within the cap")
    else:
        for r in over_cap:
            print(
                f"    {r['day']}  {r['asset_id']:<14} active={r['active']:<4} "
                f"total_rows={r['predictions']}"
            )
    return total


async def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", help="ISO date/timestamp lower bound (default: all history)")
    parser.add_argument(
        "--split",
        help="ISO timestamp; report the window before it and the window after it separately",
    )
    parser.add_argument(
        "--cap",
        type=int,
        default=2,
        help="expected max active predictions per asset per day (default 2)",
    )
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is not set; load infra/.env first", file=sys.stderr)
        return 1

    since = _parse(args.since) if args.since else datetime(1970, 1, 1, tzinfo=UTC)
    split = _parse(args.split) if args.split else None

    try:
        pool = await asyncpg.create_pool(database_url, min_size=1, max_size=2)
    except Exception as exc:  # noqa: BLE001 - a connection failure is the whole error here
        print(f"cannot connect to the database: {exc}", file=sys.stderr)
        return 1

    try:
        if split is None:
            found = await _report(pool, "ALL PREDICTIONS", since, None, cap=args.cap)
        else:
            before = await _report(
                pool, f"BEFORE {args.split}", since, split, cap=args.cap
            )
            after = await _report(
                pool, f"AFTER  {args.split}", split, None, cap=args.cap
            )
            found = before + after
            print(
                "\nCompare the two windows to attribute a behaviour change. A window that spans a "
                "deploy mixes old and new behaviour and cannot be attributed."
            )
    finally:
        await pool.close()

    return 0 if found else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
