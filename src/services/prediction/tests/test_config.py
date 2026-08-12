"""Tests for PredictionSettings fields added for E10 (max_propagation_depth)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from prediction.config import PredictionSettings


def test_max_propagation_depth_default_is_three() -> None:
    assert PredictionSettings().max_propagation_depth == 3


def test_max_propagation_depth_can_be_overridden() -> None:
    assert PredictionSettings(max_propagation_depth=5).max_propagation_depth == 5


def test_max_propagation_depth_minimum_is_one() -> None:
    with pytest.raises(ValidationError):
        PredictionSettings(max_propagation_depth=0)


def test_max_propagation_depth_maximum_is_ten() -> None:
    with pytest.raises(ValidationError):
        PredictionSettings(max_propagation_depth=11)
