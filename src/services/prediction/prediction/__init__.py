"""Feed Analyzer Prediction Service.

Consumes canonical ``EventDetected`` messages, aggregates distinct events into per-asset event-time
context windows, queries the Neo4j causal graph for firing ``CAUSES`` edges, and produces one
explainable graph-only ``PredictionMade`` per ready asset/context version.

M1 is GRAPH_ONLY (POC-6 STOP): no prediction-time LLM calls, and Verification remains the sole
producer of ``PriceRequested``.
"""
