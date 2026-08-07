# T02 — `MAX_PROPAGATION_DEPTH` Config Key

## Context

The propagation loop in T01 needs a configurable depth cap so it never runs away on a
densely connected graph. The cap is added as a `pydantic-settings` field in
`src/services/prediction/prediction/config.py`, consistent with how all other service limits
are configured.

## File to change

**`src/services/prediction/prediction/config.py`**

Read the file first. Add one field inside the existing `Settings` class:

```python
max_propagation_depth: Annotated[int, Field(ge=1, le=10)] = 3
```

- `ge=1`: propagation must run at least one hop (setting to 0 would disable it entirely,
  which is a different feature flag not needed now).
- `le=10`: safety ceiling; a 10-hop chain is unrealistic in this domain.
- Default `3` is the agreed cap from the design discussion.

The env var name follows pydantic-settings convention: `MAX_PROPAGATION_DEPTH` (uppercased
field name, no prefix unless the Settings class uses one — check the file).

## Unit test to add

**File:** `src/services/prediction/tests/test_config.py` if it exists, otherwise add to
`conftest.py` or the nearest config test.

```python
def test_max_propagation_depth_default():
    settings = Settings()
    assert settings.max_propagation_depth == 3


def test_max_propagation_depth_from_env(monkeypatch):
    monkeypatch.setenv("MAX_PROPAGATION_DEPTH", "2")
    settings = Settings()
    assert settings.max_propagation_depth == 2


def test_max_propagation_depth_zero_rejected(monkeypatch):
    monkeypatch.setenv("MAX_PROPAGATION_DEPTH", "0")
    with pytest.raises(ValidationError):
        Settings()


def test_max_propagation_depth_above_ceiling_rejected(monkeypatch):
    monkeypatch.setenv("MAX_PROPAGATION_DEPTH", "11")
    with pytest.raises(ValidationError):
        Settings()
```

## Acceptance criteria

1. `Settings().max_propagation_depth` defaults to `3`.
2. Setting `MAX_PROPAGATION_DEPTH=2` in the environment overrides the default.
3. `MAX_PROPAGATION_DEPTH=0` raises `ValidationError`.
4. `MAX_PROPAGATION_DEPTH=11` raises `ValidationError`.
5. All four unit tests pass.
6. `mypy --strict` and `ruff check` clean.

## Definition of done

- [ ] `max_propagation_depth` field added to `Settings`
- [ ] Four unit tests added and passing
- [ ] `mypy --strict` clean
- [ ] `ruff check` clean
