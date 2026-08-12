"""Tests for run_with(): dual-path learning (CAUSES + CORRELATES_WITH) and settings defaults."""

from __future__ import annotations

import os

import pytest
from shared.schemas.messages import AssetId, ConditionCode, Direction, EventType

from credibility.learning.config import LearningSettings

os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost:5432/feed")


# ---------------------------------------------------------------------------
# LearningSettings defaults
# ---------------------------------------------------------------------------


def test_learning_settings_correlation_learning_enabled_default_true() -> None:
    settings = LearningSettings()
    assert settings.correlation_learning_enabled is True


def test_learning_settings_correlation_learning_can_be_disabled() -> None:
    settings = LearningSettings(correlation_learning_enabled=False)
    assert settings.correlation_learning_enabled is False


# ---------------------------------------------------------------------------
# run_with() integration — fake build/estimate/write to avoid real I/O
# ---------------------------------------------------------------------------

from credibility.learning.run import run_with  # noqa: E402


class _FakePool:
    """Returns no rows for any query (no events, no predictions)."""

    def acquire(self) -> _FakeCorrAcquire:
        return _FakeCorrAcquire()


class _FakeCorrAcquire:
    async def __aenter__(self) -> _FakeConn:
        return _FakeConn()

    async def __aexit__(self, *_: object) -> None:
        return None


class _FakeConn:
    async def fetch(self, _query: str, *_args: object) -> list[object]:
        return []

    async def fetchval(self, *_args: object) -> None:
        return None


class _FakeGraph:
    """Graph stub: no edges; records upsert calls for CAUSES and CORRELATES_WITH."""

    def __init__(self) -> None:
        self.causes_upserts: list[tuple[EventType, ConditionCode, AssetId]] = []
        self.corr_upserts: list[tuple[AssetId, ConditionCode, AssetId]] = []

    async def get_correlation_edges(
        self, source_asset_id: AssetId, condition: ConditionCode
    ) -> list[object]:
        return []

    async def upsert_conditioned_edge(
        self,
        factor_id: EventType,
        condition: ConditionCode,
        asset_id: AssetId,
        *,
        direction: Direction,
        weight: float,
        confidence: float,
        alpha: float,
        beta: float,
    ) -> None:
        self.causes_upserts.append((factor_id, condition, asset_id))

    async def upsert_correlation_edge(
        self,
        source_asset_id: AssetId,
        condition: ConditionCode,
        target_asset_id: AssetId,
        *,
        direction: Direction,
        weight: float,
        confidence: float,
        alpha: float,
        beta: float,
    ) -> None:
        self.corr_upserts.append((source_asset_id, condition, target_asset_id))


@pytest.mark.asyncio
async def test_run_with_empty_data_returns_zero() -> None:
    settings = LearningSettings()
    graph = _FakeGraph()
    total = await run_with(_FakePool(), graph, settings)  # type: ignore[arg-type]
    assert total == 0


@pytest.mark.asyncio
async def test_run_with_skips_corr_path_when_disabled() -> None:
    settings = LearningSettings(correlation_learning_enabled=False)
    graph = _FakeGraph()
    total = await run_with(_FakePool(), graph, settings)  # type: ignore[arg-type]
    assert total == 0
    assert graph.corr_upserts == []
