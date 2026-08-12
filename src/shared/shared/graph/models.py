"""Value objects returned by the causal-graph client."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from shared.schemas.messages import AssetId, ConditionCode, Direction, EventType


class FiringEdge(BaseModel):
    """A single ``(:CausalFactor)-[:UNDER]->(:Condition)-[:CAUSES]->(:Asset)`` edge that fired.

    ``weight`` is the expert-assigned magnitude in [0, 1]; ``direction`` carries the sign.
    ``alpha``/``beta`` are the Beta-Bernoulli directional-reliability counts maintained by the
    Credibility Service (seeded at 1.0/1.0). ``condition`` is the context qualifier; when it
    is ``None`` the edge is unconditional and ``edge_id`` keeps the legacy ``FACTOR->ASSET`` form so
    older scored messages still parse. There is exactly one CAUSES edge per (factor, condition,
    asset) triple.

    ``inherited_from`` names the industry group an edge was inherited from when the asset has no
    edge of its own for that (factor, condition) pair. It is provenance only: the edge still applies
    to ``asset_id``, and its ``edge_id`` points at the group edge so Credibility updates the shared
    industry prior rather than inventing a company-specific edge that was never seeded.
    """

    model_config = ConfigDict(frozen=True)

    factor_id: EventType | None = None  # None for propagated (CORRELATES_WITH) edges
    asset_id: AssetId
    direction: Direction
    weight: Annotated[float, Field(ge=0.0, le=1.0)]
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    alpha: Annotated[float, Field(gt=0.0)]
    beta: Annotated[float, Field(gt=0.0)]
    condition: ConditionCode | None = None
    inherited_from: str | None = None
    # Set only on a propagated (CORRELATES_WITH) edge: the upstream asset the force came from.
    # It makes ``edge_id`` identify the specific correlation edge, so two upstream assets
    # converging on one target stay distinguishable when Credibility assigns credit.
    correlation_source_id: AssetId | None = None

    @property
    def edge_id(self) -> str:
        # An inherited edge identifies the GROUP edge it came from, not a per-asset edge that does
        # not exist: learning must flow back to the industry prior that actually fired.
        target = self.inherited_from or self.asset_id.value
        # A propagated edge is keyed by its source asset so it matches CorrelationEdge.edge_id
        # (``SOURCE|CONDITION->TARGET``) and Credibility can route credit to the right edge.
        if self.factor_id is None and self.correlation_source_id is not None:
            prefix = self.correlation_source_id.value
        elif self.factor_id is not None:
            prefix = self.factor_id.value
        else:
            prefix = "CORRELATION"
        if self.condition is None:
            return f"{prefix}->{target}"
        return f"{prefix}|{self.condition.value}->{target}"

    @property
    def reliability(self) -> float:
        """Beta-Bernoulli mean directional reliability, ``alpha / (alpha + beta)``."""
        return self.alpha / (self.alpha + self.beta)


class CorrelationEdge(BaseModel):
    """A single (:Asset)-[:CORRELATES_WITH {condition}]->(:Asset) edge that is active.

    ``weight`` is the expert-assigned magnitude in [0,1]; sign is carried by ``direction``.
    ``alpha``/``beta`` are the Beta-Bernoulli reliability counts (seeded 1.0/1.0).
    ``condition`` is always set (unlike CAUSES, CORRELATES_WITH has no unconditional form).
    """

    model_config = ConfigDict(frozen=True)

    source_asset_id: AssetId
    target_asset_id: AssetId
    condition: ConditionCode
    direction: Direction
    weight: Annotated[float, Field(ge=0.0, le=1.0)]
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    alpha: Annotated[float, Field(gt=0.0)]
    beta: Annotated[float, Field(gt=0.0)]

    @property
    def reliability(self) -> float:
        return self.alpha / (self.alpha + self.beta)

    @property
    def edge_id(self) -> str:
        return f"{self.source_asset_id.value}|{self.condition.value}->{self.target_asset_id.value}"
