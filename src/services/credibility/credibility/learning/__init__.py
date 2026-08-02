"""Offline structure-learning batch for the causal knowledge graph.

This package is NOT part of the always-on Credibility consumer and is NOT invoked at prediction
time (POC-6 STOP on prediction-time LLM/arbitration stands). It is a purely deterministic,
statistics-only batch job (``python -m credibility.learning.run``) that mines historical events and
realised price moves to propose data-derived conditioned ``CAUSES`` edges, refining the
expert-seeded priors already in Neo4j. No LLM, no new long-running service.
"""

from __future__ import annotations

from credibility.learning.models import EdgeEstimate, Sample

__all__ = ["EdgeEstimate", "Sample"]
