"""Feed Analyzer Ingestion Service.

Acquires permitted news content, normalizes it into a safe canonical article record, stores it
idempotently with a transactional outbox, and publishes ArticleIngested on feed.events.
See requirements/SRS-02-ingestion.md.
"""
