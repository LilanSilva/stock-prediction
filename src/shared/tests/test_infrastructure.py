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


def test_neo4j_seed_has_canonical_assets_and_minimum_edges() -> None:
    assets = (INFRA / "neo4j/init/02-seed-assets.cypher").read_text(encoding="utf-8")
    edges = (INFRA / "neo4j/init/04-seed-causal-edges.cypher").read_text(encoding="utf-8")
    assert "MERGE (a:Asset {id: 'NEM_NYSE'})" in assets
    assert "MERGE (a:Asset {id: 'XOM_NYSE'})" in assets
    assert "GC=F" not in re.sub(r"//.*", "", assets)
    assert "BZ=F" not in re.sub(r"//.*", "", assets)
    assert len(re.findall(r"MERGE \(cf\)-\[r:CAUSES\]->\(a\)", edges)) >= 15
    assert not re.search(r"r\.weight\s*=\s*-", edges)
    assert edges.count("r.alpha = 1.0, r.beta = 1.0") >= 15


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


def test_compose_does_not_embed_example_passwords() -> None:
    compose = (INFRA / "docker-compose.yml").read_text(encoding="utf-8")
    assert "changeme" not in compose
    assert "feedpassword" not in compose
    assert "name: feed-net" in compose
