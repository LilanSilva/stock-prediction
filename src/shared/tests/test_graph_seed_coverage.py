"""Every registered asset must have causal knowledge reachable in the seeded graph.

The failure this guards against is silent. Only GOLD and BRENT_OIL originally carried `CAUSES`
edges, so every company listing resolved correctly through scope inference, pricing and the session
calendar and then produced *no prediction*: the decision policy needs at least one firing edge.
Nothing errored; the pipeline just went quiet.

These tests parse the seed Cypher rather than querying a live database, so they run in CI without
infrastructure. They assert reachability of a prior, not the correctness of any particular weight.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from shared.reference import group_of, load_registry, members_of, supported_assets

SEED_DIR = Path(__file__).resolve().parents[3] / "infra" / "neo4j" / "init"
ASSET_EDGES = SEED_DIR / "04-seed-causal-edges.cypher"
CONDITIONED_EDGES = SEED_DIR / "05-seed-conditioned-edges.cypher"
GROUP_EDGES = SEED_DIR / "06-seed-group-edges.cypher"


def _seed_text(*paths: Path) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in paths if p.exists())


def _edge_targets(pattern: str, text: str) -> set[str]:
    """Ids targeted by a CAUSES edge, ignoring commented-out lines."""
    live = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("//")
    )
    return set(re.findall(pattern, live))


def _assets_with_own_edges() -> set[str]:
    text = _seed_text(ASSET_EDGES, CONDITIONED_EDGES)
    # MATCH ... (a:Asset {id: 'X'}) paired with a MERGE (cf)-[r:CAUSES...]->(a)
    return _edge_targets(r"a:Asset \{id: '([A-Z_0-9]+)'\}", text)


def _groups_with_edges() -> set[str]:
    return _edge_targets(
        r"g:AssetGroup \{id: '([A-Z_0-9]+)'\}", _seed_text(GROUP_EDGES)
    )


def _wildcard_group_factors() -> int:
    """Edges seeded against every group at once, e.g. `MATCH (cf:...), (g:AssetGroup)`."""
    text = _seed_text(GROUP_EDGES)
    live = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("//")
    )
    return len(re.findall(r"\(g:AssetGroup\)\s*$", live, flags=re.MULTILINE))


def test_group_edge_seed_exists() -> None:
    assert GROUP_EDGES.is_file(), (
        "infra/neo4j/init/06-seed-group-edges.cypher is missing; without industry-level priors "
        "every company listing produces no prediction"
    )


def test_every_asset_has_reachable_causal_knowledge() -> None:
    own = _assets_with_own_edges()
    grouped = _groups_with_edges()
    wildcard = _wildcard_group_factors() > 0

    starved = [
        asset_id
        for asset_id in supported_assets()
        if asset_id not in own and not wildcard and group_of(asset_id) not in grouped
    ]
    assert not starved, (
        f"{len(starved)} asset(s) have no asset-level edge and no edge on their group, so they can "
        f"never produce a prediction: {', '.join(sorted(starved))}"
    )


def test_every_group_can_serve_its_members() -> None:
    grouped = _groups_with_edges()
    wildcard = _wildcard_group_factors() > 0
    registry = load_registry()
    uncovered = [
        group_id
        for group_id in registry.groups
        if group_id not in grouped
        and not wildcard
        # A group whose every member carries its own edges needs no group-level prior.
        and not all(m in _assets_with_own_edges() for m in members_of(group_id))
    ]
    assert not uncovered, f"groups with no usable prior: {', '.join(sorted(uncovered))}"


def test_group_edges_reference_declared_groups() -> None:
    # A typo'd group id would seed an orphan edge that no asset can ever inherit.
    registry = load_registry()
    unknown = _groups_with_edges() - set(registry.groups)
    assert not unknown, f"group edges target undeclared groups: {', '.join(sorted(unknown))}"


def test_company_news_factor_has_a_prior() -> None:
    # CORPORATE_EARNINGS is the factor behind company-specific headlines ("Tesla acquired"), the
    # case the multi-market feature exists for. It previously had no edge at all.
    text = _seed_text(ASSET_EDGES, CONDITIONED_EDGES, GROUP_EDGES)
    assert "CORPORATE_EARNINGS" in text


@pytest.mark.parametrize(
    "asset_id", ["SAAB_B_STO", "TSLA_NASDAQ", "NOVO_B_CPH", "ASML_AMS", "SAP_ETR"]
)
def test_representative_new_listings_are_covered(asset_id: str) -> None:
    own = _assets_with_own_edges()
    grouped = _groups_with_edges()
    assert (
        asset_id in own
        or _wildcard_group_factors() > 0
        or group_of(asset_id) in grouped
    )


def test_group_edge_weights_are_valid_magnitudes() -> None:
    # weight is MAGNITUDE in [0,1]; the sign belongs to `direction`, never to the weight.
    text = _seed_text(GROUP_EDGES)
    weights = [float(w) for w in re.findall(r"r\.weight = ([0-9.]+)", text)]
    assert weights, "no group edge weights found"
    assert all(0.0 <= w <= 1.0 for w in weights), [w for w in weights if not 0.0 <= w <= 1.0]


def test_group_edges_start_from_an_uninformed_prior() -> None:
    # Expert priors carry no evidence yet: alpha/beta start at 1.0/1.0 so learning can move them.
    text = _seed_text(GROUP_EDGES)
    alphas = set(re.findall(r"r\.alpha = ([0-9.]+)", text))
    betas = set(re.findall(r"r\.beta = ([0-9.]+)", text))
    assert alphas == {"1.0"}, alphas
    assert betas == {"1.0"}, betas


def test_group_edge_directions_are_canonical() -> None:
    directions = set(re.findall(r"r\.direction = '([A-Z]+)'", _seed_text(GROUP_EDGES)))
    assert directions <= {"UP", "DOWN", "NEUTRAL"}, directions
