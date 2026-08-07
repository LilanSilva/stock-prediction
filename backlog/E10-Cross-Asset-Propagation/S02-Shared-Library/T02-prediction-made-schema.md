# T02 — Extend `PredictionMade` with Propagation Fields

## Context

`PredictionMade` in `src/shared/shared/schemas/messages.py` is the canonical message contract
crossing service boundaries (Prediction → Verification, Notification, API Gateway). It must
carry two new optional fields so downstream services can distinguish a propagated prediction
from a direct one, and so Credibility can update the correct edges.

Both fields default to backward-compatible values (`propagation_depth=0`, `propagation_chain=[]`)
so existing consumers and already-stored messages continue to work without any changes.

## Field contract

| Field | Type | Default | Meaning |
|---|---|---|---|
| `propagation_depth` | `int` | `0` | `0` = direct (CausalFactor→Asset), `1+` = propagated hop count |
| `propagation_chain` | `list[PropagationHop]` | `[]` | Ordered list of `CORRELATES_WITH` edges that produced this prediction; empty for direct predictions |

## File to change

**`src/shared/shared/schemas/messages.py`** — find the `PredictionMade` model and add the
two fields:

```python
propagation_depth: Annotated[int, Field(ge=0, default=0)] = 0
propagation_chain: list[PropagationHop] = Field(default_factory=list)
```

`PropagationHop` is added in T01 of this story and must be imported/available in the same file.

## Backward-compatibility rule

Adding an optional field with a default is a **compatible** schema change within major version
`1.0` (per `copilot-instructions.md` canonical message contract rules). The `schema_version`
field stays `1.0`. No producer or consumer breaks when receiving a message without these
fields.

## Unit tests to add

**File:** same schema test file used in T01.

```python
def test_prediction_made_defaults_to_no_propagation(make_prediction_made):
    # make_prediction_made is a pytest fixture that builds a minimal valid PredictionMade.
    # Confirm the new fields are present with their defaults when not supplied.
    pm = make_prediction_made()
    assert pm.propagation_depth == 0
    assert pm.propagation_chain == []


def test_prediction_made_accepts_propagation_chain(make_prediction_made):
    hop = PropagationHop(
        source_asset_id=AssetId.XOM_NYSE,
        target_asset_id=AssetId.NEM_NYSE,
        condition=ConditionCode.UPSTREAM_UP,
        direction=Direction.DOWN,
        edge_weight=0.45,
    )
    pm = make_prediction_made(propagation_depth=1, propagation_chain=[hop])
    assert pm.propagation_depth == 1
    assert len(pm.propagation_chain) == 1
    assert pm.propagation_chain[0].source_asset_id == AssetId.XOM_NYSE


def test_prediction_made_round_trips_with_chain(make_prediction_made):
    hop = PropagationHop(
        source_asset_id=AssetId.XOM_NYSE,
        target_asset_id=AssetId.NEM_NYSE,
        condition=ConditionCode.UPSTREAM_UP,
        direction=Direction.DOWN,
        edge_weight=0.45,
    )
    pm = make_prediction_made(propagation_depth=1, propagation_chain=[hop])
    assert PredictionMade.model_validate_json(pm.model_dump_json()) == pm


def test_prediction_made_negative_depth_rejected(make_prediction_made):
    with pytest.raises(ValidationError):
        make_prediction_made(propagation_depth=-1)
```

If `make_prediction_made` fixture does not exist, look for the existing `PredictionMade`
construction in `tests/conftest.py` and use it as a template. Do not create a duplicate fixture.

## Acceptance criteria

1. `PredictionMade` has `propagation_depth: int` (default `0`) and
   `propagation_chain: list[PropagationHop]` (default `[]`).
2. A `PredictionMade` built without these fields deserialises with `propagation_depth == 0`
   and `propagation_chain == []`.
3. A `PredictionMade` with a populated `propagation_chain` round-trips through
   `model_dump_json()` / `model_validate_json()` without loss.
4. `propagation_depth < 0` raises `ValidationError`.
5. All four new unit tests pass.
6. All pre-existing `PredictionMade` tests still pass.
7. `mypy --strict` and `ruff check` clean.

## Definition of done

- [ ] `propagation_depth` field added with correct type and default
- [ ] `propagation_chain` field added with correct type and default
- [ ] Four unit tests added and passing
- [ ] No existing test broken
- [ ] `mypy --strict` clean
- [ ] `ruff check` clean
