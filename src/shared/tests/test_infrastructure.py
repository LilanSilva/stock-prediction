"""Static contract checks for the E01 infrastructure definitions."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, cast

REPO_ROOT = Path(__file__).parents[3]
INFRA = REPO_ROOT / "infra"

WORK_QUEUE_BINDINGS = {
    "cleansing.articles": "article.ingested",
    "prediction.events": "event.detected",
    "verification.predictions": "prediction.made",
    "market-data.price-requests": "price.requested",
    "verification.prices": "price.observed",
    "credibility.scored": "prediction.scored",
    "market-data.intraday-requests": "intraday.requested",
    "verification.intraday-prices": "intraday.observed",
}
LIVE_QUEUE_BINDINGS = {
    "gateway.predictions.live": "prediction.made",
    "gateway.scored.live": "prediction.scored",
}


def _definitions() -> dict[str, Any]:
    raw = json.loads((INFRA / "rabbitmq/definitions.json").read_text(encoding="utf-8"))
    return cast(dict[str, Any], raw)


def test_postgres_initializes_one_database_with_six_schemas_and_pgvector() -> None:
    sql = (INFRA / "postgres/01-init-database.sql").read_text(encoding="utf-8")
    assert "CREATE EXTENSION IF NOT EXISTS vector" in sql
    for schema in (
        "ingestion",
        "cleansing",
        "prediction",
        "market_data",
        "verification",
        "credibility",
    ):
        assert re.search(rf"CREATE SCHEMA IF NOT EXISTS\s+{schema}\b", sql)
    assert "CREATE DATABASE" not in sql


def _seed(name: str) -> str:
    return (INFRA / f"neo4j/init/{name}").read_text(encoding="utf-8")


def _strip_comments(cypher: str) -> str:
    return re.sub(r"//.*", "", cypher)


def test_neo4j_seed_has_canonical_assets_and_minimum_edges() -> None:
    assets = _seed("02-seed-assets.cypher")
    assert "MERGE (a:Asset {id: 'NEM_NYSE'})" in assets
    assert "MERGE (a:Asset {id: 'XOM_NYSE'})" in assets
    assert "GC=F" not in _strip_comments(assets)
    assert "BZ=F" not in _strip_comments(assets)

    # The causal edges live in 05 (conditioned), 06 (industry priors) and 07 (newer event types).
    # 04 is retired: it targeted the GOLD and BRENT_OIL asset nodes, which 02 never creates, so
    # every statement in it was a silent no-op (E12 S02).
    edges = "".join(
        _seed(name)
        for name in (
            "05-seed-conditioned-edges.cypher",
            "06-seed-group-edges.cypher",
            "07-seed-new-event-type-edges.cypher",
        )
    )
    merges = re.findall(
        r"MERGE \(cf\)-\[r:CAUSES(?: \{condition: '[A-Z_]+'\})?\]->\([ag]\)", edges
    )
    assert len(merges) >= 15
    # weight carries magnitude only; the sign lives in `direction`.
    assert not re.search(r"r\.weight\s*=\s*-", edges)
    assert edges.count("r.alpha = 1.0, r.beta = 1.0") >= 15


def test_no_seed_statement_targets_a_node_that_is_never_created() -> None:
    """The defect that made an entire seed file a no-op for months (E12 S02 defect 4).

    `MATCH (a:Asset {id: 'GOLD'}) MERGE ...` binds nothing when the node does not exist, and
    cypher-shell still exits 0, so twelve expert priors were discarded with no error anywhere.
    Asserted
    statically here and again against the live graph by 09-verify-seed.cypher.
    """
    created = _strip_comments(_seed("02-seed-assets.cypher"))
    created_ids = set(re.findall(r"\{id: '([A-Z_0-9=.]+)'\}", created))
    assert {"NEM_NYSE", "XOM_NYSE"} <= created_ids

    for name in (
        "04-seed-causal-edges.cypher",
        "05-seed-conditioned-edges.cypher",
        "06-seed-group-edges.cypher",
        "07-seed-new-event-type-edges.cypher",
        "08-seed-correlation-edges.cypher",
    ):
        body = _strip_comments(_seed(name))
        referenced = set(re.findall(r"\(\s*a\d?\s*:Asset \{id: '([A-Z_0-9=.]+)'\}", body))
        # DELETE statements may reference a retired id in order to clean it up; MATCH...MERGE may
        # not.
        if "DELETE" in body:
            referenced -= {"GOLD", "BRENT_OIL"}
        missing = referenced - created_ids
        assert not missing, f"{name} targets Asset id(s) that 02 never creates: {sorted(missing)}"


def test_seed_verification_file_is_present_and_aborts_on_failure() -> None:
    """09 must actually fail the seed, not just report. `apoc.util.validate` raises."""
    verify = _seed("09-verify-seed.cypher")
    assert verify.count("apoc.util.validate") >= 5
    # Sorts last in the glob the seed container iterates, so it runs after everything it checks.
    names = sorted(p.name for p in (INFRA / "neo4j/init").glob("*.cypher"))
    assert names[-1] == "09-verify-seed.cypher"


def test_rabbitmq_topology_matches_canonical_contract() -> None:
    definitions = _definitions()
    exchanges = {item["name"]: item for item in definitions["exchanges"]}
    assert exchanges["feed.events"]["type"] == "topic"
    assert exchanges["feed.events"]["durable"] is True
    assert exchanges["feed.dlx"]["durable"] is True

    queues = {item["name"]: item for item in definitions["queues"]}
    bindings = {
        (item["source"], item["destination"], item["routing_key"])
        for item in definitions["bindings"]
    }

    for queue_name, routing_key in WORK_QUEUE_BINDINGS.items():
        queue = queues[queue_name]
        assert queue["durable"] is True
        assert queue["arguments"]["x-dead-letter-exchange"] == "feed.dlx"
        assert queue_name + ".dlq" in queues
        assert queues[queue_name + ".dlq"]["durable"] is True
        assert ("feed.events", queue_name, routing_key) in bindings
        assert ("feed.dlx", queue_name + ".dlq", routing_key) in bindings

    for queue_name, routing_key in LIVE_QUEUE_BINDINGS.items():
        assert queues[queue_name]["durable"] is False
        assert ("feed.events", queue_name, routing_key) in bindings


def _asset_ids(registry: dict[str, Any]) -> set[str]:
    grouped = {
        asset["asset_id"]
        for group in registry.get("groups", [])
        for asset in group.get("assets", [])
    }
    return grouped | {asset["asset_id"] for asset in registry.get("standalone_assets", [])}


def test_deployed_asset_registry_matches_the_shared_source_of_truth() -> None:
    """``infra/assets/assets.json`` must not drift from the packaged registry.

    Regression: the deployed file is mounted at ``/config/assets.json`` and ``ASSET_REGISTRY_PATH``
    makes it override the wheel-packaged copy. It had drifted to an older generation still naming
    ``GOLD``/``BRENT_OIL`` while the code had migrated to the ``NEM_NYSE``/``XOM_NYSE`` equity
    proxies, so every service that imported an asset constant by name crashed on startup
    (``'NEM_NYSE' is not a canonical asset id``) as soon as its image was rebuilt. Nothing caught it
    because the running containers predated the migration.
    """
    deployed = cast(
        dict[str, Any],
        json.loads((INFRA / "assets/assets.json").read_text(encoding="utf-8")),
    )
    source = cast(
        dict[str, Any],
        json.loads(
            (REPO_ROOT / "src/shared/shared/reference/assets.json").read_text(encoding="utf-8")
        ),
    )
    assert deployed["registry_version"] == source["registry_version"]
    assert _asset_ids(deployed) == _asset_ids(source), (
        "infra/assets/assets.json has drifted from src/shared/shared/reference/assets.json; "
        "copy the shared registry over the deployed one so mounted config matches the code"
    )


def test_compose_does_not_embed_example_passwords() -> None:
    compose = (INFRA / "docker-compose.yml").read_text(encoding="utf-8")
    assert "changeme" not in compose
    assert "feedpassword" not in compose
    assert "name: feed-net" in compose
