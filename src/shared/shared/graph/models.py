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

    factor_id: EventType
    asset_id: AssetId
    direction: Direction
    weight: Annotated[float, Field(ge=0.0, le=1.0)]
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    alpha: Annotated[float, Field(gt=0.0)]
    beta: Annotated[float, Field(gt=0.0)]
    condition: ConditionCode | None = None
    inherited_from: str | None = None

    @property
    def edge_id(self) -> str:
        # An inherited edge identifies the GROUP edge it came from, not a per-asset edge that does
        # not exist: learning must flow back to the industry prior that actually fired.
        target = self.inherited_from or self.asset_id.value
        if self.condition is None:
            return f"{self.factor_id.value}->{target}"
        return f"{self.factor_id.value}|{self.condition.value}->{target}"

    @property
    def reliability(self) -> float:
        """Beta-Bernoulli mean directional reliability, ``alpha / (alpha + beta)``."""
        return self.alpha / (self.alpha + self.beta)
