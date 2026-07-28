"""Feed Analyzer Verification Service.

Sole owner of prediction evaluation. Consumes ``PredictionMade`` and, for each new prediction,
resolves canonical baseline/settlement sessions without look-ahead and publishes exactly one
dual-session ``PriceRequested``. Consumes the returned ``PriceObserved``, scores it close-to-close,
and publishes ``PredictionScored`` exactly once. Postgres + RabbitMQ only (no Neo4j).
"""
