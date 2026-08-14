"""Environment-backed configuration for the Prediction Service."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class PredictionSettings(BaseSettings):
    """Settings read from ``PREDICTION_*`` plus the shared infra variables."""

    model_config = SettingsConfigDict(env_prefix="PREDICTION_", extra="ignore")

    # Shared infra connection strings use their conventional unprefixed names.
    database_url: str = Field(validation_alias="DATABASE_URL")
    rabbitmq_url: str = Field(validation_alias="RABBITMQ_URL")
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")

    events_queue: str = Field(default="prediction.events")

    # Event-time context aggregation. Distinct events for one asset within a tumbling window of this
    # size form one versioned context (ADR-001). Article/event count never triggers a prediction.
    context_window_minutes: int = Field(default=15, gt=0)
    # Grace after a window's end before it is eligible to close, allowing slightly late events in.
    close_grace_minutes: int = Field(default=5, ge=0)
    close_interval_seconds: int = Field(default=60, gt=0)

    # Decision policy (graph-only). A firing set with net directional ratio below the deadband is
    # reported as NEUTRAL; magnitude buckets split the agreeing-edge average expert weight.
    # Applies to the evidence-weighted confidence (see decision.decide), not to a bare agreement
    # ratio: below it the decision is NEUTRAL. The old ratio-based form was unreachable for a single
    # firing edge, so NEUTRAL was never emitted.
    decision_deadband: float = Field(default=0.15, ge=0.0, lt=1.0)
    # Evidence half-point for the confidence mass term: total edge strength equal to this value
    # yields a mass of 0.5. Lower makes the system more confident on thin evidence.
    confidence_evidence_halfpoint: float = Field(default=0.5, gt=0.0)
    # Per-asset stances allowed on one local trading day. A further stance is emitted only when the
    # direction changes, so the cap is reached at two opposing calls. A non-trading day is always
    # collapsed to a single active stance regardless of this value (supersede-and-withdraw).
    max_daily_predictions_per_asset: int = Field(default=2, ge=1)
    magnitude_small_max: float = Field(default=0.40, gt=0.0, lt=1.0)
    magnitude_medium_max: float = Field(default=0.70, gt=0.0, le=1.0)

    # Scope-B price gate. A RESOLUTION-driven DOWN is only emitted when the asset's latest close is
    # elevated versus the mean of the prior sessions by at least this fraction; otherwise there is
    # no risk premium to unwind. Recent closes are read from the Market Data service.
    market_data_base_url: str = Field(default="http://feed-market-data:8000")
    price_lookback_sessions: int = Field(default=10, gt=0)
    price_elevated_threshold_pct: float = Field(default=0.01, ge=0.0)
    market_data_timeout_seconds: float = Field(default=5.0, gt=0)

    db_pool_min_size: int = Field(default=1, ge=1)
    db_pool_max_size: int = Field(default=5, ge=1)

    # Cross-asset propagation depth cap (S03/T02). Depth 1 = one hop, 10 = safety ceiling.
    max_propagation_depth: Annotated[int, Field(ge=1, le=10)] = 3
    # Minimum confidence a DIRECT decision needs before its direction is propagated across
    # CORRELATES_WITH edges (E12 PRD-60). A propagated prediction carries no causal factor — it
    # is an inference drawn from another asset's inference — so a weak source is amplified rather
    # than diluted. On 2026-08-12 propagation produced 39 of 113 predictions (35%) at a 32% hit
    # rate, against 47% for direct ones.
    #
    # Choosing the value needs the confidence arithmetic, because the usable range is narrow. With
    # alpha=beta=1 on every edge (Credibility moves `weight`, never the Beta counts), reliability
    # is a constant 0.5, so a single firing edge of expert weight w gives:
    #     total = 0.5w,  consensus = 1.0,  mass = 0.5w / (0.5w + 0.5),  confidence = w / (w + 1)
    # which is 0.33 at w=0.5 and cannot exceed 0.50 even at w=1.0. So the whole single-edge range
    # lives below 0.5, and a threshold of 0.5 would disable propagation outright — 92% of decisions
    # rest on one edge — rather than gate it.
    #
    # 0.30 is set at the boundary that separates expert-strength evidence from evidence the learner
    # has walked DOWN. It admits a single edge of weight >= 0.43, so every seeded prior still
    # propagates (the 0.50 group priors give 0.33), while an edge Credibility has driven down does
    # not: NEM_NYSE's learned MILITARY_CONFLICT weight of 0.107 gives 0.10, and LMT_NYSE's 0.374
    # gives 0.27. Those are the edges that kept being wrong, so they are exactly the ones that must
    # not seed a second, derivative prediction.
    #
    # It is deliberately a floor on evidence strength and NOT a contradiction filter. The audit's
    # propagated predictions were mostly contradictions, and those are handled by the suppression
    # rule in the pipeline (PRD-61); no confidence threshold would have caught them without also
    # switching off propagation for the seeded graph.
    propagation_min_confidence: float = Field(default=0.30, ge=0.0, le=1.0)
