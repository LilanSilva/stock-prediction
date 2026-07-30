"""Feed Analyzer Credibility Service.

Consumes ``PredictionScored`` (routing key ``prediction.scored``) from the ``credibility.scored``
queue and closes the learning loop: it applies a Beta-Bernoulli Bayesian update to two independent
dimensions of credibility and records a full audit trail with 95% credible intervals.

  1. Causal-graph edge weights in Neo4j (``alpha``/``beta`` on the ``(:CausalFactor)-[:CAUSES]->``
     ``(:Asset)`` edge) via proportional credit from each ``ContributingEdge.influence_weight``.
  2. News-source credibility in Postgres (``credibility.credibility``) via equal credit across the
     message's ``source_ids``.

The service publishes nothing; its output is the write-back to the knowledge graph and Postgres.
"""

from __future__ import annotations

__version__ = "0.1.0"
