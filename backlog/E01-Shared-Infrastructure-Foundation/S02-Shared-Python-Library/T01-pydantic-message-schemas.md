# T01: Pydantic Message Schemas

## Context

This task creates all six RabbitMQ message contracts as Pydantic v2 models in `src/shared/schemas/messages.py`. These schemas are the single source of truth for what flows between services. Every service that publishes or consumes a queue message imports from this module. If a field name changes here, it breaks every service that uses it — so these schemas must be carefully designed and thoroughly tested.

This is the foundational task of the shared library story (S02). All other tasks in S02 and every task in every service epic depend on these schemas being defined and importable.

## Background

### Message flow overview

```
Ingestion → ArticleIngested → [raw-news]
Cleansing → EventDetected  → [events]
Prediction → PredictionMade → [predictions]
Verification → PriceRequested → [price-requests]
MarketData → PriceObserved → [prices]
Verification → PredictionScored → [scored-predictions]
```

### Pydantic v2 key differences from v1

- Use `model_validator` instead of `root_validator`
- Use `field_validator` instead of `validator`
- `model_dump()` instead of `dict()`
- `model_dump_json()` for JSON serialization
- `model_validate()` instead of `parse_obj()`
- `model_validate_json()` instead of `parse_raw()`
- Annotated types: `from pydantic import Field` with `Annotated`
- `ConfigDict` instead of `class Config`

### Correlation ID

Every message carries a `correlation_id: str` (UUID4 format). This ID is set by the Ingestion Service when it creates an `ArticleIngested` message and propagates unchanged through every downstream message. It enables end-to-end tracing across all services for a single piece of news.

## Inputs

No runtime inputs. This task defines the data structures. The message field names are specified in the system overview.

## Outputs

- `src/shared/schemas/messages.py` — all six Pydantic v2 message models
- `src/shared/schemas/__init__.py` — re-exports all models
- `tests/test_schemas.py` — pytest test suite for all models

## Technical Requirements

### Package dependencies (`src/shared/pyproject.toml`)

```toml
[project]
name = "feed-analyzer-shared"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "pydantic>=2.7,<3",
    "pydantic-settings>=2.3,<3",
]

[project.optional-dependencies]
dev = [
    "pytest>=8",
    "pytest-asyncio>=0.23",
    "ruff>=0.5",
    "mypy>=1.10",
]
```

### Model: `ArticleIngested`

Published to queue `raw-news` by the Ingestion Service.

```python
class ArticleIngested(BaseModel):
    article_id: str                    # UUID4, set by Ingestion
    source: str                        # e.g. 'gdelt', 'di', 'dn', 'aftonbladet', 'svd'
    url: str                           # canonical article URL
    title: str                         # normalized (UTF-8, whitespace stripped)
    body: str                          # normalized article body text
    published_at: datetime             # UTC, timezone-aware
    language: str                      # ISO 639-1: 'en' or 'sv'
    country: str                       # ISO 3166-1 alpha-2: 'US' or 'SE'
    raw_html: str | None = None        # original HTML, may be None for API sources
    correlation_id: str                # UUID4 propagated end-to-end
    schema_version: str = "1.0"        # for future migrations
```

### Model: `EventDetected`

Published to queue `events` by the Cleansing Service.

```python
class FactConflict(BaseModel):
    field: str                         # which field has conflicting data
    values: list[str]                  # the conflicting values from different sources
    resolution: str | None = None      # how the conflict was resolved (or None if unresolved)

class SourceRef(BaseModel):
    article_id: str
    source: str
    url: str
    title: str
    published_at: datetime

class EventDetected(BaseModel):
    event_id: str                      # UUID4, set by Cleansing
    canonical_summary: str             # LLM-generated merged summary
    event_type: str                    # e.g. 'war_escalation', 'rate_decision', 'earnings'
    actor: str | None = None           # e.g. 'Russia', 'Federal Reserve'
    action: str | None = None          # e.g. 'invaded', 'raised_rates'
    object: str | None = None          # e.g. 'Ukraine', 'interest_rates'
    entities: list[str] = Field(default_factory=list)   # named entities
    affected_assets: list[str] = Field(default_factory=list)  # asset IDs from graph
    first_seen: datetime               # UTC, timezone-aware
    source_count: int                  # number of articles merged into this event
    sources: list[SourceRef] = Field(default_factory=list)
    fact_conflicts: list[FactConflict] = Field(default_factory=list)
    correlation_id: str
    schema_version: str = "1.0"
```

### Model: `PredictionMade`

Published to queue `predictions` by the Prediction Service.

```python
class DirectionEnum(str, Enum):
    UP = "UP"
    DOWN = "DOWN"
    NEUTRAL = "NEUTRAL"

class MagnitudeBucket(str, Enum):
    SMALL = "SMALL"       # 0.3% - 1%
    MEDIUM = "MEDIUM"     # 1% - 3%
    LARGE = "LARGE"       # > 3%

class ContributingEdge(BaseModel):
    causal_factor_id: str
    asset_id: str
    direction: DirectionEnum
    weight: float                      # -1.0 to 1.0
    confidence: float                  # 0.0 to 1.0

class PredictionMade(BaseModel):
    prediction_id: str                 # UUID4, set by Prediction
    event_ids: list[str]               # EventDetected.event_id values that triggered this
    asset: str                         # asset ID from graph (e.g. 'gold', 'sp500')
    direction: DirectionEnum
    magnitude_bucket: MagnitudeBucket
    confidence: float                  # 0.0 to 1.0, LLM-assigned
    time_horizon: str                  # e.g. '24h', '48h', '1w'
    rationale: str                     # LLM explanation
    contributing_edges: list[ContributingEdge] = Field(default_factory=list)
    correlation_id: str
    created_at: datetime               # UTC, timezone-aware
    schema_version: str = "1.0"
```

### Model: `PriceRequested`

Published to queue `price-requests` by the Verification Service.

```python
class PriceRequested(BaseModel):
    request_id: str                    # UUID4, set by Verification
    prediction_id: str                 # links back to PredictionMade
    asset: str                         # asset ID (matches PredictionMade.asset)
    window_close_at: datetime          # UTC, when to fetch the closing price
    correlation_id: str
    schema_version: str = "1.0"
```

### Model: `PriceObserved`

Published to queue `prices` by the Market Data Service.

```python
class PriceObserved(BaseModel):
    request_id: str                    # echoes PriceRequested.request_id
    prediction_id: str                 # echoes PriceRequested.prediction_id
    asset: str
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None        # may be None for forex
    observed_at: datetime              # UTC, the candle's timestamp
    correlation_id: str
    schema_version: str = "1.0"
```

### Model: `PredictionScored`

Published to queue `scored-predictions` by the Verification Service.

```python
class PredictionScored(BaseModel):
    prediction_id: str
    asset: str
    predicted_direction: DirectionEnum
    actual_direction: DirectionEnum
    predicted_magnitude: MagnitudeBucket
    actual_magnitude: MagnitudeBucket | None = None    # may be None if direction was NEUTRAL
    is_correct: bool
    score: float                       # 0.0 = wrong, 1.0 = correct, 0.5 = partial
    contributing_edges: list[ContributingEdge] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)   # source names that contributed
    scored_at: datetime                # UTC, timezone-aware
    correlation_id: str
    schema_version: str = "1.0"
```

### Common base class

All models should inherit from a `FeedMessage` base:

```python
class FeedMessage(BaseModel):
    model_config = ConfigDict(
        frozen=True,           # immutable after creation
        populate_by_name=True, # allow field aliases
        str_strip_whitespace=True,
    )

    @model_validator(mode='after')
    def validate_correlation_id_format(self) -> 'FeedMessage':
        # Validate correlation_id is a valid UUID4
        import uuid
        try:
            uuid.UUID(self.correlation_id, version=4)
        except (ValueError, AttributeError):
            raise ValueError(f"correlation_id must be a valid UUID4: {self.correlation_id}")
        return self
```

### Datetime handling

All `datetime` fields must be timezone-aware UTC. Add a validator to the base class or each model:

```python
@field_validator('*', mode='before')
@classmethod
def ensure_utc(cls, v: Any) -> Any:
    if isinstance(v, datetime) and v.tzinfo is None:
        return v.replace(tzinfo=timezone.utc)
    return v
```

Alternatively, use `AwareDatetime` from `pydantic` (Pydantic v2 built-in).

### Serialization helpers

Add class methods to `FeedMessage`:

```python
@classmethod
def from_amqp_body(cls, body: bytes) -> 'FeedMessage':
    """Deserialize from AMQP message body (UTF-8 JSON bytes)."""
    return cls.model_validate_json(body.decode('utf-8'))

def to_amqp_body(self) -> bytes:
    """Serialize to AMQP message body (UTF-8 JSON bytes)."""
    return self.model_dump_json().encode('utf-8')
```

## Acceptance Criteria

1. `from shared.schemas.messages import ArticleIngested, EventDetected, PredictionMade, PriceRequested, PriceObserved, PredictionScored` imports without error.
2. Each model can be instantiated with all required fields and serialized to JSON via `model_dump_json()`.
3. Each model can be deserialized from its own JSON output via `model_validate_json()` without data loss (round-trip test).
4. An invalid `correlation_id` (not UUID4 format) raises `ValidationError`.
5. A naive datetime (no timezone) is automatically converted to UTC-aware datetime.
6. `frozen=True` config means attempting `msg.article_id = 'new_value'` raises `ValidationError`.
7. `from_amqp_body(msg.to_amqp_body())` returns an equal model instance for all six message types.
8. All models pass `mypy --strict` type checking.
9. `pytest tests/test_schemas.py` passes with 100% of defined test cases.
10. `ruff check src/shared/schemas/` returns no violations.

## Implementation Notes

- Use `uuid.uuid4()` to generate IDs in tests, not hardcoded strings.
- `DirectionEnum` and `MagnitudeBucket` enums are shared across multiple models. Define them once at module level and import them in both `PredictionMade` and `PredictionScored`.
- The `model_config = ConfigDict(frozen=True)` setting is important for safety: message objects flowing through the pipeline should never be mutated after creation.
- For `schema_version`, use a default of `"1.0"` and document that version bumps require a migration guide. Do not add validation logic for schema_version in this task — keep it simple.
- `FactConflict`, `SourceRef`, `ContributingEdge` are nested models. Pydantic v2 handles nested model serialization automatically.
- In tests, use `pytest.mark.parametrize` to test all six message types with the same round-trip test logic rather than writing six separate tests.
- The `body` field in `ArticleIngested` can be very long (10,000+ characters for full article text). Do not add a max-length validator — let the application handle truncation if needed.
- Import `from __future__ import annotations` at the top of `messages.py` to enable forward references cleanly.

## Definition of Done

- [x] `src/shared/schemas/messages.py` defines all six message models plus `FeedMessage` base, `DirectionEnum`, `MagnitudeBucket`, `FactConflict`, `SourceRef`, `ContributingEdge`
- [x] `src/shared/schemas/__init__.py` re-exports all public names
- [x] `src/shared/pyproject.toml` declares `pydantic>=2.7,<3` as a dependency
- [x] `tests/test_schemas.py` tests round-trip serialization for all six models
- [x] `tests/test_schemas.py` tests correlation_id validation
- [x] `tests/test_schemas.py` tests datetime UTC coercion
- [x] All tests pass: `pytest tests/test_schemas.py -v`
- [x] `ruff check src/shared/` returns 0 violations
- [x] `mypy src/shared/ --strict` returns 0 errors
