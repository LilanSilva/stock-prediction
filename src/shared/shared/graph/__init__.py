"""Shared Neo4j causal-graph access for services that reason over the knowledge graph.

Only Prediction (read) and Credibility (read/write) use this package, so the `neo4j` driver is an
optional install (the shared `graph` extra) and is imported lazily inside the client. Services that
never touch the graph (Ingestion, Cleansing) carry no Neo4j dependency.

The canonical schema (seeded in infra/neo4j/init) is:
    (:CausalFactor {id})-[:CAUSES {direction, weight, confidence, alpha, beta}]->(:Asset {id})
where CausalFactor.id is a canonical EventType and Asset.id is a canonical AssetId. Provider symbols
(e.g. XAUUSD, UKOIL) never appear on graph nodes; they live only inside Market Data adapters.
"""

from __future__ import annotations

from shared.graph.client import CausalGraphClient
from shared.graph.exceptions import GraphConfigurationError, GraphTransportError
from shared.graph.models import CorrelationEdge, FiringEdge
from shared.graph.settings import Neo4jSettings

__all__ = [
    "CausalGraphClient",
    "CorrelationEdge",
    "FiringEdge",
    "GraphConfigurationError",
    "GraphTransportError",
    "Neo4jSettings",
]
