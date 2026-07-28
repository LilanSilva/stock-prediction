"""Typed exceptions for the Prediction Service."""

from __future__ import annotations


class PredictionError(Exception):
    """Base class for prediction-service errors."""


class InvalidEventError(PredictionError):
    """Raised when a consumed event carries non-canonical or unusable identifiers."""


class GraphInferenceError(PredictionError):
    """Raised when the causal-graph inference step fails and should be retried."""
