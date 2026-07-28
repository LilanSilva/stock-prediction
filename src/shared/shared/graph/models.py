"""Value objects returned by the causal-graph client."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from shared.schemas.messages import AssetId, Direction, EventType


class FiringEdge(BaseModel):
    """A single ``(:CausalFactor)-[:CAUSES]->(:Asset)`` edge that fired for an event type.

    ``weight`` is the expert-assigned magnitude in [0, 1]; ``direction`` carries the sign.
    ``alpha``/``beta`` are the Beta-Bernoulli directional-reliability counts maintained by the
    Credibility Service (seeded at 1.0/1.0). ``edge_id`` is a stable, deterministic business key
    (there is exactly one CAUSES edge per factor/asset pair).
    """

    model_config = ConfigDict(frozen=True)

    factor_id: EventType
    asset_id: AssetId
    direction: Direction
    weight: Annotated[float, Field(ge=0.0, le=1.0)]
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    alpha: Annotated[float, Field(gt=0.0)]
    beta: Annotated[float, Field(gt=0.0)]

    @property
    def edge_id(self) -> str:
        return f"{self.factor_id.value}->{self.asset_id.value}"

    @property
    def reliability(self) -> float:
        """Beta-Bernoulli mean directional reliability, ``alpha / (alpha + beta)``."""
        return self.alpha / (self.alpha + self.beta)
