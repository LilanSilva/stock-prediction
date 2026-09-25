"""Canonical message contracts as Pydantic v2 models.

These models are the executable source of truth for the contracts described in
requirements/SRS-01-shared-foundation.md section 8 and each service's SRS section 8. Every field
name, enum value, and routing key here matches those documents; where they disagree, this file wins
and the specification is a defect. The legacy per-task schema examples (single-DB `close`,
`window_close_at`, signed weights, `source` instead of `source_id`, etc.) are non-authoritative and
intentionally NOT used.

Envelope (all messages): message_id, correlation_id, causation_id, occurred_at, schema_version.
Enum values use uppercase snake case. Asset values are canonical asset IDs (GOLD, BRENT_OIL).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    HttpUrl,
    model_validator,
)

# Re-exported (redundant alias form) so `from shared.schemas.messages import AssetId` keeps working
# for the ~100 existing call sites now that AssetId lives in its own module.
from shared.schemas.asset_id import AssetId as AssetId

# --- Routing keys (requirements/SRS-01-shared-foundation.md sec 8.4 "RabbitMQ topology") ---

EXCHANGE = "feed.events"


class RoutingKey(StrEnum):
    ARTICLE_INGESTED = "article.ingested"
    EVENT_DETECTED = "event.detected"
    PREDICTION_MADE = "prediction.made"
    PRICE_REQUESTED = "price.requested"
    PRICE_OBSERVED = "price.observed"
    PREDICTION_SCORED = "prediction.scored"
    INTRADAY_REQUESTED = "intraday.requested"
    INTRADAY_OBSERVED = "intraday.observed"
    PRICE_SAMPLE_OBSERVED = "price.sample.observed"


# --- Canonical enums ---


# AssetId is no longer a closed enum: canonical assets are declared in the JSON asset registry so
# companies and markets can be added without a code change (see shared.schemas.asset_id). It stays
# str-compatible, so `asset.value` and `AssetId.GOLD` call sites are unaffected.


class EventType(StrEnum):
    """requirements/REF-01-event-taxonomy.md (taxonomy version 1.3)."""

    MILITARY_CONFLICT = "MILITARY_CONFLICT"
    STRAIT_CLOSURE = "STRAIT_CLOSURE"
    SUPPLY_DISRUPTION = "SUPPLY_DISRUPTION"
    SANCTIONS = "SANCTIONS"
    RATE_DECISION = "RATE_DECISION"
    INFLATION_CHANGE = "INFLATION_CHANGE"
    RECESSION_SIGNAL = "RECESSION_SIGNAL"
    CORPORATE_EARNINGS = "CORPORATE_EARNINGS"
    POLITICAL_TRANSITION = "POLITICAL_TRANSITION"
    NATURAL_DISASTER = "NATURAL_DISASTER"
    # Company-level events
    CORPORATE_ACQUISITION = "CORPORATE_ACQUISITION"
    EXECUTIVE_CHANGE = "EXECUTIVE_CHANGE"
    REGULATORY_ACTION = "REGULATORY_ACTION"
    DEBT_CRISIS = "DEBT_CRISIS"
    RESTRUCTURING = "RESTRUCTURING"
    LEGAL_DISPUTE = "LEGAL_DISPUTE"
    PRODUCT_RECALL = "PRODUCT_RECALL"
    DIVIDEND_CHANGE = "DIVIDEND_CHANGE"
    CONTRACT_WIN = "CONTRACT_WIN"
    SHARE_BUYBACK = "SHARE_BUYBACK"
    IPO_LISTING = "IPO_LISTING"
    CYBERSECURITY_INCIDENT = "CYBERSECURITY_INCIDENT"
    # Macro / country-level events
    TRADE_POLICY = "TRADE_POLICY"
    FISCAL_POLICY = "FISCAL_POLICY"
    CURRENCY_CRISIS = "CURRENCY_CRISIS"
    SOVEREIGN_DEBT = "SOVEREIGN_DEBT"
    GEOPOLITICAL_TENSION = "GEOPOLITICAL_TENSION"
    COMMODITY_PRICE_SHOCK = "COMMODITY_PRICE_SHOCK"
    # Market / financial system events
    ECONOMIC_DATA_RELEASE = "ECONOMIC_DATA_RELEASE"
    PANDEMIC_OUTBREAK = "PANDEMIC_OUTBREAK"
    ENERGY_POLICY = "ENERGY_POLICY"
    OTHER = "OTHER"
    # Non-financial events. These carry no causal edge and no asset mapping, so they are the
    # explicit reject bucket: an article typed here is recognised as irrelevant rather than merely
    # unmapped. Keeping them distinct from OTHER restores OTHER to its documented meaning ("valid
    # event not yet represented") and stops generic keywords from typing sport as a market event.
    SPORT = "SPORT"
    ENTERTAINMENT = "ENTERTAINMENT"
    LIFESTYLE = "LIFESTYLE"


class Direction(StrEnum):
    UP = "UP"
    DOWN = "DOWN"
    NEUTRAL = "NEUTRAL"


class Magnitude(StrEnum):
    SMALL = "SMALL"
    MEDIUM = "MEDIUM"
    LARGE = "LARGE"


class Horizon(StrEnum):
    ONE_TRADING_DAY = "ONE_TRADING_DAY"


class ExtractionMethod(StrEnum):
    LOCAL = "LOCAL"
    LLM_ASSISTED = "LLM_ASSISTED"


class DecisionMethod(StrEnum):
    # POC-6 STOP: M1 emits GRAPH_ONLY. LLM_ARBITRATED stays in the contract for a future approved
    # controlled hypothesis but must not be produced in M1.
    GRAPH_ONLY = "GRAPH_ONLY"
    LLM_ARBITRATED = "LLM_ARBITRATED"


class PriceKind(StrEnum):
    PROVIDER_DAILY_CLOSE = "PROVIDER_DAILY_CLOSE"
    OFFICIAL_SETTLEMENT = "OFFICIAL_SETTLEMENT"


class LlmStatus(StrEnum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class EventPolarity(StrEnum):
    """Whether an event is the onset of a factor or its resolution/reversal.

    OCCURRENCE is the default and preserves pre-2.x behavior (the factor is happening). RESOLUTION
    marks de-escalation/negation (e.g. a planned strike called off), which inverts the sign of the
    factor's causal edge at decision time.
    """

    OCCURRENCE = "OCCURRENCE"
    RESOLUTION = "RESOLUTION"


class ConditionCode(StrEnum):
    """Context qualifiers that gate which causal edge fires (stored as a CAUSES-edge property)."""

    TRANSPORT_AFFECTED = "TRANSPORT_AFFECTED"
    SAFE_HAVEN_ONLY = "SAFE_HAVEN_ONLY"
    RISK_PREMIUM_ELEVATED = "RISK_PREMIUM_ELEVATED"
    UPSTREAM_UP = "UPSTREAM_UP"    # source asset predicted UP in this pipeline run
    UPSTREAM_DOWN = "UPSTREAM_DOWN"  # source asset predicted DOWN in this pipeline run


def _assume_utc_for_naive(value: object) -> object:
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _normalize_to_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


UtcDatetime = Annotated[
    AwareDatetime,
    BeforeValidator(_assume_utc_for_naive),
    AfterValidator(_normalize_to_utc),
]
NonEmptyStr = Annotated[str, Field(min_length=1)]


# --- Shared value objects ---


class SourceRef(BaseModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    article_id: uuid.UUID
    source_id: NonEmptyStr
    canonical_url: HttpUrl
    title: NonEmptyStr
    published_at: UtcDatetime


class FactConflictValue(BaseModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    source_id: NonEmptyStr
    value: NonEmptyStr


class FactConflict(BaseModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    field: NonEmptyStr
    values: list[FactConflictValue]
    resolution: str | None = None


class LlmMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    prompt_version: NonEmptyStr
    model: NonEmptyStr
    context_hash: NonEmptyStr
    input_tokens: Annotated[int, Field(ge=0)]
    output_tokens: Annotated[int, Field(ge=0)]
    latency_ms: Annotated[int, Field(ge=0)]
    attempt_count: Annotated[int, Field(ge=1)]
    status: LlmStatus


class ContributingEdge(BaseModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    edge_id: NonEmptyStr
    direction: Direction
    current_weight: Annotated[float, Field(ge=0.0, le=1.0)]
    influence_weight: Annotated[float, Field(ge=0.0, le=1.0)]
    path: NonEmptyStr


class PropagationHop(BaseModel):
    """One fired (:Asset)-[:CORRELATES_WITH]->(:Asset) edge in a propagation chain.

    Carried in PredictionMade.propagation_chain so Credibility can update the correct
    CORRELATES_WITH edge when the prediction is scored.
    """

    model_config = ConfigDict(frozen=True)

    source_asset_id: AssetId
    target_asset_id: AssetId
    condition: ConditionCode          # UPSTREAM_UP or UPSTREAM_DOWN
    direction: Direction              # direction contributed to the target (after force summation)
    edge_weight: float                # expert weight of the fired edge


class CloseObservation(BaseModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    session: date
    close: Annotated[Decimal, Field(gt=0)]
    provider_bar_time: AwareDatetime | None = None
    fetched_at: UtcDatetime
    source: NonEmptyStr
    provider_symbol: NonEmptyStr
    price_kind: PriceKind
    is_adjusted: bool
    registry_version: NonEmptyStr


# --- Envelope base ---


class FeedMessage(BaseModel):
    """Common envelope for every domain message.

    Immutable after construction. All datetime fields are timezone-aware UTC: naive datetimes are
    coerced to UTC rather than rejected, so producers that forget tzinfo still emit valid messages.
    """

    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        str_strip_whitespace=True,
        use_enum_values=False,
    )

    message_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    correlation_id: uuid.UUID
    causation_id: uuid.UUID | None = None
    occurred_at: UtcDatetime
    schema_version: NonEmptyStr = "1.0"

    @classmethod
    def from_amqp_body(cls, body: bytes) -> "FeedMessage":  # noqa: UP037
        """Deserialize from an AMQP message body (UTF-8 JSON bytes)."""
        return cls.model_validate_json(body.decode("utf-8"))

    def to_amqp_body(self) -> bytes:
        """Serialize to an AMQP message body (UTF-8 JSON bytes)."""
        return self.model_dump_json().encode("utf-8")


# --- Messages ---


class ArticleIngested(FeedMessage):
    """Routing key: article.ingested. Producer: Ingestion. Raw HTML is never published."""

    article_id: uuid.UUID
    source_id: NonEmptyStr
    canonical_url: HttpUrl
    title: NonEmptyStr
    body: str
    published_at: UtcDatetime
    language: Annotated[str, Field(pattern=r"^[a-z]{2}$")]
    country: Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
    content_hash: NonEmptyStr


class EventDetected(FeedMessage):
    """Routing key: event.detected. Producer: Cleansing."""

    event_id: uuid.UUID
    cluster_id: uuid.UUID
    canonical_summary: NonEmptyStr
    event_type: EventType
    actor: str | None = None
    action: str | None = None
    object: str | None = None
    entities: list[str] = Field(default_factory=list)
    affected_asset_ids: list[AssetId] = Field(default_factory=list)
    # Added in 1.x (backward-compatible defaults): conditional-causality qualifiers.
    polarity: EventPolarity = EventPolarity.OCCURRENCE
    context_tags: list[ConditionCode] = Field(default_factory=list)
    first_seen_at: UtcDatetime
    last_seen_at: UtcDatetime
    sources: list[SourceRef] = Field(default_factory=list)
    fact_conflicts: list[FactConflict] = Field(default_factory=list)
    extraction_method: ExtractionMethod
    llm_metadata: LlmMetadata | None = None


class PredictionMade(FeedMessage):
    """Routing key: prediction.made. Producer: Prediction. M1 decision_method is GRAPH_ONLY."""

    prediction_id: uuid.UUID
    context_id: uuid.UUID
    context_version: int
    event_ids: list[uuid.UUID]
    asset_id: AssetId
    direction: Direction
    magnitude: Magnitude
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    horizon: Horizon
    rationale: Annotated[str, Field(min_length=1, max_length=2000)]
    contributing_edges: list[ContributingEdge] = Field(default_factory=list)
    decision_at: UtcDatetime
    # Time this delivery was attempted, not an assertion of broker or user receipt.
    publication_attempt_at: UtcDatetime | None = None
    supersedes_prediction_id: uuid.UUID | None = None
    decision_method: DecisionMethod
    llm_metadata: LlmMetadata | None = None
    # Propagation fields (backward-compatible defaults; 0/[] preserves existing message parsing).
    propagation_depth: Annotated[int, Field(ge=0)] = 0
    propagation_chain: list[PropagationHop] = Field(default_factory=list)


class PriceRequested(FeedMessage):
    """Routing key: price.requested. Sole producer: Verification. Carries both sessions."""

    request_id: uuid.UUID
    prediction_id: uuid.UUID
    asset_id: AssetId
    baseline_session: date
    settlement_session: date
    market_calendar: NonEmptyStr


class PriceObserved(FeedMessage):
    """Routing key: price.observed. Producer: Market Data. Carries both closes."""

    request_id: uuid.UUID
    prediction_id: uuid.UUID
    asset_id: AssetId
    baseline: CloseObservation
    settlement: CloseObservation


class PredictionScored(FeedMessage):
    """Routing key: prediction.scored. Producer: Verification."""

    prediction_id: uuid.UUID
    context_id: uuid.UUID
    asset_id: AssetId
    predicted_direction: Direction
    actual_direction: Direction
    predicted_magnitude: Magnitude
    actual_magnitude: Magnitude
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    actual_return: float
    is_correct: bool
    score: Annotated[float, Field(ge=0.0, le=1.0)]
    contributing_edges: list[ContributingEdge] = Field(default_factory=list)
    source_ids: list[NonEmptyStr] = Field(default_factory=list)
    baseline: CloseObservation
    settlement: CloseObservation
    scored_at: UtcDatetime
    # Propagation fields forwarded from PredictionMade (backward-compatible defaults).
    propagation_chain: list[PropagationHop] = Field(default_factory=list)


# Map each message model to the routing key it is published with.
class IntradayBar(BaseModel):
    """One completed, unadjusted, UTC minute bar. Timestamp denotes its opening."""

    model_config = ConfigDict(frozen=True)
    start: UtcDatetime
    open: Annotated[Decimal, Field(gt=0, allow_inf_nan=False)]
    high: Annotated[Decimal, Field(gt=0, allow_inf_nan=False)]
    low: Annotated[Decimal, Field(gt=0, allow_inf_nan=False)]
    close: Annotated[Decimal, Field(gt=0, allow_inf_nan=False)]

    @model_validator(mode="after")
    def coherent_bar(self) -> IntradayBar:
        if self.start.second or self.start.microsecond:
            raise ValueError("minute bar timestamp must be minute aligned")
        if not self.low <= min(self.open, self.close) <= max(self.open, self.close) <= self.high:
            raise ValueError("inconsistent OHLC bounds")
        return self


class IntradayRequested(FeedMessage):
    """One shared regular-session stream, requested only by Verification."""

    stream_id: uuid.UUID
    asset_id: AssetId
    registry_version: NonEmptyStr
    calendar_id: NonEmptyStr
    opens_at: UtcDatetime
    closes_at: UtcDatetime

    @model_validator(mode="after")
    def valid_session(self) -> IntradayRequested:
        seconds = (self.closes_at - self.opens_at).total_seconds()
        if not 0 < seconds <= 24 * 3600 or seconds % 60:
            raise ValueError("invalid regular session interval")
        if self.opens_at.second or self.opens_at.microsecond:
            raise ValueError("session must start on a minute boundary")
        return self


class IntradayObserved(FeedMessage):
    """Ordered stream revisions; bars contain additions/corrections, never implicit deletions."""

    stream_id: uuid.UUID
    asset_id: AssetId
    registry_version: NonEmptyStr
    revision: Annotated[int, Field(ge=1)]
    bars: Annotated[list[IntradayBar], Field(max_length=1500)]
    final: bool = False
    failure: str | None = None


class PriceSampleObserved(FeedMessage):
    """A point observation, never an OHLC bar or an asserted official close."""

    sample_id: uuid.UUID
    asset_id: AssetId
    mapping_version: NonEmptyStr
    registry_version: NonEmptyStr
    session: date
    opens_at: UtcDatetime
    closes_at: UtcDatetime
    scheduled_at: UtcDatetime
    observed_at: UtcDatetime
    provider_quote_at: AwareDatetime | None = None
    price: Annotated[Decimal, Field(gt=0, allow_inf_nan=False)]
    quote_delay_seconds: Annotated[int, Field(ge=0)] | None = None
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
    quote_unit: NonEmptyStr
    source: Literal["avanza"] = "avanza"
    interval_seconds: Literal[900] = 900
    kind: Literal["REGULAR", "CLOSE_CHECK"]
    market_state: Literal[
        "PRE_OPEN", "REGULAR_OPEN", "REGULAR_CLOSED", "EXTENDED_HOURS", "HALTED", "UNKNOWN"
    ]
    quality: Literal["FRESH", "STALE", "FRESHNESS_UNKNOWN", "SESSION_MISMATCH"]

    @model_validator(mode="after")
    def valid_sample(self) -> PriceSampleObserved:
        if not self.opens_at < self.closes_at:
            raise ValueError("invalid sample session")
        if self.observed_at < self.scheduled_at:
            raise ValueError("sample observed before its slot")
        if self.provider_quote_at and self.provider_quote_at > self.observed_at:
            raise ValueError("quote timestamp is in the future")
        if self.quality == "FRESH" and self.provider_quote_at is None:
            raise ValueError("freshness requires a provider timestamp")
        if self.kind == "REGULAR" and not self.opens_at <= self.scheduled_at < self.closes_at:
            raise ValueError("regular sample outside its session")
        if self.kind == "CLOSE_CHECK" and not self.closes_at <= self.scheduled_at:
            raise ValueError("close check before close")
        return self


ROUTING_KEY_BY_MESSAGE: dict[type[FeedMessage], RoutingKey] = {
    ArticleIngested: RoutingKey.ARTICLE_INGESTED,
    EventDetected: RoutingKey.EVENT_DETECTED,
    PredictionMade: RoutingKey.PREDICTION_MADE,
    PriceRequested: RoutingKey.PRICE_REQUESTED,
    PriceObserved: RoutingKey.PRICE_OBSERVED,
    PredictionScored: RoutingKey.PREDICTION_SCORED,
    IntradayRequested: RoutingKey.INTRADAY_REQUESTED,
    IntradayObserved: RoutingKey.INTRADAY_OBSERVED,
    PriceSampleObserved: RoutingKey.PRICE_SAMPLE_OBSERVED,
}
