"""Read-only, reproducible research evidence exports; these are not labeled training datasets."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import asyncpg

from prediction.research import CAPTURE_VERSION, canonical_json, content_hash


async def export_evidence(
    pool: asyncpg.Pool, output: Path, cutoff: datetime,
) -> dict[str, object]:
    """Write a new directory; manifest.json is the commit marker for a complete export.

    An interrupted or corrupt export has no manifest. Existing directories are never overwritten.
    The DB snapshot excludes evidence committed after the transaction began, including rows whose
    feature time predates the export cutoff but whose durable research receipt does not.
    """
    if cutoff.tzinfo is None or cutoff.utcoffset() is None:
        raise ValueError("export cutoff must be timezone-aware")
    if cutoff > datetime.now(UTC):
        raise ValueError("export cutoff cannot be in the future")
    output.mkdir(parents=True, exist_ok=False)
    digest = hashlib.sha256()
    qualities: Counter[str] = Counter()
    results: Counter[str] = Counter()
    count = 0
    async with pool.acquire() as conn:
        async with conn.transaction(isolation="repeatable_read", readonly=True):
            with (output / "opportunities.jsonl").open("xb") as handle:
                async for row in conn.cursor(
                    """SELECT o.*, p.result_status, p.payload AS result,
                              p.available_at AS result_received_at
                       FROM prediction.research_opportunities o
                       JOIN prediction.research_predictions p USING (opportunity_id)
                       WHERE o.feature_cutoff <= $1 AND o.created_at <= $1
                         AND p.available_at <= $1 AND o.capture_version = $2
                         AND p.predictor_id = 'KG_LIVE_CAPTURE'
                       ORDER BY o.feature_cutoff, o.opportunity_id""", cutoff, CAPTURE_VERSION,
                ):
                    if content_hash(row["snapshot"]) != row["snapshot_hash"]:
                        raise ValueError(f"snapshot integrity failure: {row['opportunity_id']}")
                    item = {
                        "opportunity_id": str(row["opportunity_id"]),
                        "snapshot_hash": row["snapshot_hash"],
                        "snapshot": json.loads(row["snapshot"]),
                        "quality": row["quality"],
                        "kg_result": json.loads(row["result"]),
                        "research_received_at": row["result_received_at"].isoformat(),
                    }
                    line = (canonical_json(item) + "\n").encode("utf-8")
                    handle.write(line)
                    digest.update(line)
                    count += 1
                    qualities[row["quality"]] += 1
                    results[row["result_status"]] += 1
                handle.flush()
                os.fsync(handle.fileno())
    manifest: dict[str, object] = {
        "format_version": "research-evidence-export-v1",
        "capture_version": CAPTURE_VERSION,
        "cutoff": cutoff.astimezone(UTC).isoformat(),
        "file": "opportunities.jsonl", "sha256": digest.hexdigest(),
        "rows": count, "quality_counts": dict(qualities), "kg_status_counts": dict(results),
        "training_eligible": False,
        "blockers": ["NO_POINT_IN_TIME_MARKET_FEATURES", "NO_FINALIZED_RESEARCH_LABELS"],
    }
    # Atomic completion marker: a process crash while writing cannot leave a partial manifest.
    temporary = output / "manifest.pending"
    with temporary.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(canonical_json(manifest) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.rename(output / "manifest.json")
    return manifest


async def _run(output: Path, cutoff: datetime) -> None:
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=1)
    try:
        manifest = await export_evidence(pool, output, cutoff)
        print(canonical_json(manifest))
    finally:
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New export directory")
    parser.add_argument("--cutoff", type=datetime.fromisoformat, required=True)
    args = parser.parse_args()
    asyncio.run(_run(args.output, args.cutoff))


if __name__ == "__main__":
    main()
