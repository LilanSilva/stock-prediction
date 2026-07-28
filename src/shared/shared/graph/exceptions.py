"""Typed errors for the shared causal-graph client."""

from __future__ import annotations


class GraphError(Exception):
    """Base class for causal-graph access errors."""


class GraphConfigurationError(GraphError):
    """Raised when the graph client is used without required configuration or the driver."""


class GraphTransportError(GraphError):
    """Raised when a Neo4j request fails at the transport level (normalized from driver errors)."""
