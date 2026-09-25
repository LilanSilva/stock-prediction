"""Versioned point-sample evidence. Never claims continuous price-path coverage."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from shared.schemas.messages import Direction, PriceSampleObserved

from verification.intraday_policy import SessionWindow


class SamplePolicy(BaseModel):
    model_config = ConfigDict(frozen=True)
    version: Literal["SAMPLED_TARGET_V1"] = "SAMPLED_TARGET_V1"
    interval_seconds: Literal[900] = 900
    target_return: Decimal = Field(default=Decimal("0.003"), gt=0, lt=1)
    neutral_band: Decimal = Field(default=Decimal("0.003"), gt=0, lt=1)
    max_baseline_delay_seconds: int = 1020
    max_quote_age_seconds: int = 120
    delivery_grace_seconds: int = 600


class SampleResult(BaseModel):
    status: Literal["PENDING", "FINAL", "UNSCORABLE", "WITHDRAWN"] = "PENDING"
    outcome: str | None = None
    baseline_at: datetime | None = None
    baseline_price: Decimal | None = None
    baseline_delay_seconds: float | None = None
    mapping_version: str | None = None
    observed_hit: bool | None = None
    first_hit_at: datetime | None = None
    last_observed_return: Decimal | None = None
    last_observed_at: datetime | None = None
    expected_samples: int = 0
    observed_samples: int = 0
    coverage_complete: bool = False
    maximum_gap_seconds: float | None = None


def evaluate_samples(
    start: datetime,
    window: SessionWindow,
    direction: Direction,
    policy: SamplePolicy,
    currency: str,
    registry_version: str,
    samples: list[PriceSampleObserved],
    *,
    final: bool,
    previous: SampleResult | None = None,
) -> SampleResult:
    start = max(start, window.opens_at)
    expected = []
    at = window.opens_at
    while at < window.closes_at:
        if at >= start:
            expected.append(at)
        at += timedelta(seconds=policy.interval_seconds)
    if len(expected) < 2:
        return SampleResult(status="UNSCORABLE", outcome="INSUFFICIENT_WINDOW")
    eligible = sorted(
        (
            s
            for s in samples
            if (
                s.kind == "REGULAR"
                and s.quality == "FRESH"
                and s.market_state == "REGULAR_OPEN"
                and s.opens_at == window.opens_at
                and s.closes_at == window.closes_at
                and s.currency == currency
                and s.quote_unit == currency
                and s.registry_version == registry_version
                and s.scheduled_at in expected
                and s.provider_quote_at is not None
                and start <= s.provider_quote_at <= s.observed_at
                and start <= s.observed_at < window.closes_at
                and (s.observed_at - s.provider_quote_at).total_seconds()
                <= policy.max_quote_age_seconds
                and (s.observed_at - s.scheduled_at).total_seconds() <= 120
            )
        ),
        key=lambda s: (s.observed_at, str(s.sample_id)),
    )
    if not eligible:
        return SampleResult(
            status="UNSCORABLE" if final else "PENDING",
            outcome="NO_BASELINE",
            expected_samples=len(expected),
        )
    baseline = eligible[0]
    delay = (baseline.observed_at - start).total_seconds()
    if delay > policy.max_baseline_delay_seconds:
        return SampleResult(
            status="UNSCORABLE" if final else "PENDING", outcome="BASELINE_TOO_LATE"
        )
    if (
        previous
        and previous.baseline_at
        and (
            previous.baseline_at != baseline.observed_at
            or previous.baseline_price != baseline.price
            or previous.mapping_version != baseline.mapping_version
        )
    ):
        return SampleResult(status="UNSCORABLE", outcome="BASELINE_REVISED")
    if len({s.mapping_version for s in eligible}) > 1:
        return SampleResult(status="UNSCORABLE", outcome="MAPPING_CHANGED")
    by_slot: dict[datetime, PriceSampleObserved] = {}
    for sample in eligible:
        if sample.scheduled_at in by_slot and by_slot[sample.scheduled_at] != sample:
            return SampleResult(status="UNSCORABLE", outcome="CONFLICTING_SLOT")
        by_slot[sample.scheduled_at] = sample
    complete = set(by_slot) == set(expected)
    subsequent = eligible[1:]
    returns = [(s, s.price / baseline.price - 1) for s in subsequent]
    hit = next(
        (
            s
            for s, r in returns
            if (
                (direction == Direction.UP and r >= policy.target_return)
                or (direction == Direction.DOWN and r <= -policy.target_return)
            )
        ),
        None,
    )
    breached = any(abs(r) > policy.neutral_band for _, r in returns)
    outcome = "INSUFFICIENT_SAMPLES"
    if len(eligible) >= 2:
        if direction == Direction.NEUTRAL:
            outcome = (
                "BAND_BREACH_OBSERVED"
                if breached
                else ("WITHIN_BAND_AT_SAMPLES" if complete else "INSUFFICIENT_SAMPLES")
            )
        elif hit:
            outcome = "OBSERVED_HIT"
        elif complete:
            outcome = "TARGET_NOT_OBSERVED"
    # Include start/end gaps; maximum gap is a sampling diagnostic, not continuous coverage.
    times = [start, *[s.observed_at for s in eligible], window.closes_at]
    return SampleResult(
        status="FINAL" if final else "PENDING",
        outcome=outcome,
        baseline_at=baseline.observed_at,
        baseline_price=baseline.price,
        baseline_delay_seconds=delay,
        mapping_version=baseline.mapping_version,
        observed_hit=bool(hit) if direction != Direction.NEUTRAL else None,
        first_hit_at=hit.observed_at if hit else None,
        last_observed_return=eligible[-1].price / baseline.price - 1 if subsequent else None,
        last_observed_at=eligible[-1].observed_at if subsequent else None,
        expected_samples=len(expected),
        observed_samples=len(by_slot),
        coverage_complete=complete,
        maximum_gap_seconds=max(
            (b - a).total_seconds() for a, b in zip(times, times[1:], strict=False)
        ),
    )
