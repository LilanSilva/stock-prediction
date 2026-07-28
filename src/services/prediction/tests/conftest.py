"""Pytest fixtures: provide dummy infra env so PredictionSettings() constructs in unit tests."""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost:5432/feed")
os.environ.setdefault("RABBITMQ_URL", "amqp://user:pass@localhost:5672/")
