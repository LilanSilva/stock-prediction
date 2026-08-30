"""E12 post-deploy smoke test: real articles in, expected classification out.

Runs the shipped cleansing logic in-process against the live asset registry, then queries the live
Neo4j graph for the ADR-006 gating rule. Not a substitute for the test suites — this is the "does the
deployed thing actually behave" check for the precision rules CLN-63 - CLN-68
(requirements/SRS-03-cleansing.md) and the propagation rules PRD-60 - PRD-62
(requirements/SRS-04-prediction.md).

Every case is a real article from the 2026-08-12 prediction audit. The negatives are articles that
produced predictions they should not have; the positives are the ones that were genuinely justified and
must keep working.

Usage (needs NEO4J_PASSWORD and NEO4J_URI in the environment):
    python scripts/smoke-e12-prediction-quality.py

Exit code 0 when every check passes, 1 otherwise.
"""

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src/shared"))
sys.path.insert(0, str(_ROOT / "src/services/cleansing"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from cleansing.taxonomy import classify_text, resolve_scope
from shared.graph.client import CausalGraphClient
from shared.graph.settings import Neo4jSettings
from shared.schemas.messages import AssetId, ConditionCode, EventType

# (label, title, body, expect_assets)
CASES = [
    ("negative: WWE", "Damian Priest & R-Truth, The War Raiders and The MFTs clash in title bout",
     "Backed by WWE Hall of Famer Haku, they defend the WWE Tag Team Title.", False),
    ("negative: Mozart",
     ("Over 200 Mozart Figurines Stolen From Salzburg Art Installation, "
      "Forcing Early Closure Of Garden Display"),
     "The Mozarteum Foundation confirmed 210 vanished.", False),
    ("negative: stroller", "Stroller running linked to lower overuse injury rates for new parents",
     "Parents who ran with a stroller were less likely to sustain an overuse injury.", False),
    ("negative: eclipse", "Kvällens solförmörkelse kan bli extra dramatisk",
     "Solförmörkelser har gjort slut på krig och förändrat vår syn på universum.", False),
    ("negative: housing", "DN Debatt. ”En hel generation är på väg att stängas ute från villorna”",
     "Unga drömmer om att bo i villa men priserna är för höga.", False),
    ("negative: Twitch", "Twitch Now Trains Amazon's Generative AI Models On Your Channel By Default",
     "The setting can be disabled, but it's turned on by default.", False),
    ("positive: Ukraine war", "Nytt ryskt drag – luftkriget mot Ukraina trappas upp",
     "Kriget i Ukraina har skiftat fokus och Rysslands attacker skördar fler civila liv.", True),
    ("positive: Gaza strike", "Ny israelisk attack på Gazaremsan",
     "En befälhavare inom Hamas har dödats i en israelisk flygattack.", True),
    ("positive: gold forecast", "Gold expected to trade around $4,500/oz by end of 2026",
     "The LBMA survey of 16 analysts forecasts spot gold to average $4,604 per ounce.", True),
    ("positive: CPI", "Guldlyft efter USA-inflationen: ”Perfekt läge”",
     "Guldpriset lyfte efter att inflationssiffran kom in i linje med förväntningarna.", True),
]


async def main() -> int:
    failures = 0
    print("=" * 78)
    print("CLASSIFICATION SMOKE TEST")
    print("=" * 78)
    for label, title, body, expect in CASES:
        event_type, _ = classify_text(title, body)
        assets = resolve_scope(title, event_type).assets
        got = bool(assets)
        ok = got == expect
        failures += 0 if ok else 1
        print(f"{'PASS' if ok else 'FAIL'}  {label:26} {event_type.value:22} "
              f"{[a.value for a in assets] if assets else '(no assets)'}")

    print()
    print("=" * 78)
    print("GRAPH GATING SMOKE TEST (live Neo4j)")
    print("=" * 78)
    graph = CausalGraphClient(Neo4jSettings())
    await graph.connect()
    try:
        # ADR-006: a distant conflict (SAFE_HAVEN_ONLY) must not LIFT oil (UP direction).
        # The structure learner may create a DOWN edge for oil in safe-haven scenarios — that is
        # valid learning (oil equities de-risk into gold). The invariant is direction, not presence.
        oil_safe_haven = await graph.get_firing_edges(
            EventType.MILITARY_CONFLICT, [AssetId.XOM_NYSE],
            conditions={ConditionCode.SAFE_HAVEN_ONLY}
        )
        oil_up = [e for e in oil_safe_haven if e.direction.value == "UP"]
        ok = not oil_up
        failures += 0 if ok else 1
        print(f"{'PASS' if ok else 'FAIL'}  {'distant conflict must NOT lift oil (UP)':36} "
              f"{len(oil_safe_haven)} edge(s), {len(oil_up)} UP")

        checks = [
            ("distant conflict MUST move gold", AssetId.NEM_NYSE,
             ConditionCode.SAFE_HAVEN_ONLY, True),
            ("transport conflict MUST move oil", AssetId.XOM_NYSE,
             ConditionCode.TRANSPORT_AFFECTED, True),
        ]
        for label, asset, condition, expect in checks:
            edges = await graph.get_firing_edges(
                EventType.MILITARY_CONFLICT, [asset], conditions={condition}
            )
            ok = bool(edges) == expect
            failures += 0 if ok else 1
            print(f"{'PASS' if ok else 'FAIL'}  {label:36} {len(edges)} edge(s)")

        # No factor may fire twice for one asset.
        for condition in (ConditionCode.SAFE_HAVEN_ONLY, ConditionCode.TRANSPORT_AFFECTED):
            edges = await graph.get_firing_edges(
                EventType.MILITARY_CONFLICT, conditions={condition}
            )
            per_asset: dict[AssetId, int] = {}
            for edge in edges:
                per_asset[edge.asset_id] = per_asset.get(edge.asset_id, 0) + 1
            dupes = {a.value: n for a, n in per_asset.items() if n > 1}
            ok = not dupes
            failures += 0 if ok else 1
            print(f"{'PASS' if ok else 'FAIL'}  no double-firing under "
                  f"{condition.value:20} {dupes or '{}'}")
    finally:
        await graph.close()

    print()
    print(f"{'ALL SMOKE TESTS PASSED' if not failures else f'{failures} FAILURE(S)'}")
    return 1 if failures else 0


sys.exit(asyncio.run(main()))
