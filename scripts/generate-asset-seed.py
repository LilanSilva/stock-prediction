#!/usr/bin/env python
"""Generate the Neo4j asset/group seed Cypher from the JSON asset registry.

The seed used to hardcode asset ids, so every registry edit needed a matching Cypher edit -- and a
missed one leaves the graph unable to serve edges for an asset the pipeline predicts. Generating it
keeps ``assets.json`` the single source of truth.

Usage:
    python scripts/generate-asset-seed.py                # rewrite the seed file in place
    python scripts/generate-asset-seed.py --check        # fail if the file is stale (for CI)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src" / "shared"))

# Imported after the sys.path insert above so the shared package resolves without installation.
from shared.reference.loader import load_registry

SEED_PATH = REPO_ROOT / "infra" / "neo4j" / "init" / "02-seed-assets.cypher"

HEADER = """\
// Seed canonical assets and industry groups (GENERATED -- do not edit by hand).
//
// Regenerate with:  python scripts/generate-asset-seed.py
// Source of truth:  src/shared/shared/reference/assets.json
//
// Every asset belongs to exactly one (:AssetGroup) via MEMBER_OF. Causal edges may be attached at
// either level: prediction reads an asset's own CAUSES edges when they exist and otherwise inherits
// its group's, so a newly added listing predicts from day one instead of waiting for the offline
// learner to accumulate enough samples for a company-specific edge.
//
// Asset.id is a CANONICAL asset id. Provider symbols (XAUUSD, SAAB-B.ST, ...) live only in the
// registry and in Market Data adapters, never on graph nodes.
//
// MERGE keeps this idempotent so the seed container can re-run safely.
"""


def render(registry_path: Path | None = None) -> str:
    registry = load_registry(registry_path)
    lines = [HEADER]

    for group in registry.groups.values():
        lines.append(f"\n// --- {group.display_name} ---")
        lines.append(
            f"MERGE (g:AssetGroup {{id: '{group.group_id}'}})\n"
            f"SET g.name = {_quote(group.display_name)};"
        )
        for asset_id in group.members:
            entry = registry.assets[asset_id]
            lines.append(
                f"MERGE (a:Asset {{id: '{entry.asset_id}'}})\n"
                f"SET a.name = {_quote(entry.name)},"
                f" a.asset_class = '{_asset_class(entry.code)}',"
                f" a.currency = '{entry.currency}',"
                f" a.market = '{entry.expected_exchange}',"
                f" a.timezone = '{entry.timezone}';"
            )
            lines.append(
                f"MATCH (a:Asset {{id: '{entry.asset_id}'}}), "
                f"(g:AssetGroup {{id: '{group.group_id}'}})\n"
                f"MERGE (a)-[:MEMBER_OF]->(g);"
            )

    return "\n".join(lines) + "\n"


def _quote(value: str) -> str:
    """Single-quoted Cypher string literal with embedded quotes escaped."""
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _asset_class(code: str) -> str:
    exchange = code.split(":", 1)[0]
    return "commodity" if exchange in {"COMEX", "NYMEX"} else "equity"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the seed file is stale")
    parser.add_argument("--file", type=Path, default=None, help="registry file (default: packaged)")
    args = parser.parse_args()

    rendered = render(args.file)

    if args.check:
        current = SEED_PATH.read_text(encoding="utf-8") if SEED_PATH.exists() else ""
        if current != rendered:
            print(f"STALE  {SEED_PATH.relative_to(REPO_ROOT)}")
            print("       run: python scripts/generate-asset-seed.py")
            return 1
        print(f"OK     {SEED_PATH.relative_to(REPO_ROOT)} matches the registry")
        return 0

    SEED_PATH.write_text(rendered, encoding="utf-8")
    registry = load_registry(args.file)
    print(f"wrote {SEED_PATH.relative_to(REPO_ROOT)}")
    print(f"  {len(registry.groups)} groups, {len(registry.assets)} assets")
    return 0


if __name__ == "__main__":
    sys.exit(main())
