"""Avanza-first reads. Derived values retain sample provenance and explicit coverage."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg
import structlog
from pydantic import BaseModel, ConfigDict
from shared.reference import resolve
from shared.schemas.messages import (
    AssetId,
    CloseObservation,
    IntradayBar,
    PriceKind,
    PriceSampleObserved,
    SampleProvenance,
)

from market_data.adapters.router import PriceAdapter
from market_data.exceptions import PriceNotYetAvailableError
from market_data.sessions import is_session_complete
from market_data.snapshots.calendar import Session, session_for, slots
from market_data.snapshots.config import Listing, load_mappings

logger = structlog.get_logger(__name__)
MINUTE = timedelta(minutes=1)
GRACE = timedelta(seconds=120)


class AvanzaUnavailable(Exception):
    """Missing or invalid Avanza evidence; a finance fallback may satisfy the request."""


class AvanzaPending(PriceNotYetAvailableError):
    """An unfinished session or a read within its allowed deadline is not a failure."""


class SessionSummary(BaseModel):
    model_config = ConfigDict(frozen=True)
    asset_id: AssetId
    session: date
    source: str = "avanza"
    price_basis: str = "AVANZA_SAMPLED_OHLC_V1"
    currency: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    provisional: bool
    complete: bool
    observed_samples: int
    expected_samples: int
    missing_slots: list[datetime]
    first_sample: SampleProvenance
    last_sample: SampleProvenance


def provenance(sample: PriceSampleObserved) -> SampleProvenance:
    if sample.quality not in ("FRESH", "FRESHNESS_UNKNOWN"):
        raise AvanzaUnavailable("INVALID_SAMPLE_QUALITY")
    return SampleProvenance(
        sample_id=sample.sample_id,
        scheduled_at=sample.scheduled_at,
        observed_at=sample.observed_at,
        expires_at=min(
            sample.scheduled_at + timedelta(seconds=sample.interval_seconds), sample.closes_at
        ),
        mapping_version=sample.mapping_version,
        interval_seconds=sample.interval_seconds,
        currency=sample.currency,
        provider_quote_at=sample.provider_quote_at,
        quote_delay_seconds=sample.quote_delay_seconds,
        quality=sample.quality,
    )


def validate_samples(
    samples: list[PriceSampleObserved],
    asset_id: AssetId,
    window: Session,
    currency: str,
    registry_version: str,
    now: datetime,
) -> list[PriceSampleObserved]:
    regular = sorted(
        (s for s in samples if s.kind == "REGULAR" and s.observed_at <= now),
        key=lambda s: s.scheduled_at,
    )
    identities = {(s.mapping_version, s.interval_seconds) for s in regular}
    if len(identities) > 1:
        raise AvanzaUnavailable("MIXED_MAPPING_OR_CADENCE")
    if len({s.scheduled_at for s in regular}) != len(regular):
        raise AvanzaUnavailable("DUPLICATE_SAMPLE_SLOT")
    for sample in regular:
        if (
            sample.asset_id != asset_id
            or sample.currency != currency
            or sample.quote_unit != currency
            or sample.registry_version != registry_version
            or sample.session != window.session
            or sample.opens_at != window.opens_at
            or sample.closes_at != window.closes_at
        ):
            raise AvanzaUnavailable("SAMPLE_IDENTITY_MISMATCH")
        if (
            sample.market_state != "REGULAR_OPEN"
            or sample.quality not in ("FRESH", "FRESHNESS_UNKNOWN")
            or not sample.scheduled_at <= sample.observed_at < window.closes_at
            or sample.observed_at - sample.scheduled_at > GRACE
            or (sample.scheduled_at - window.opens_at).total_seconds() % sample.interval_seconds
        ):
            raise AvanzaUnavailable("INVALID_SAMPLE_EVIDENCE")
    return regular


def summarize(
    asset_id: AssetId,
    window: Session,
    samples: list[PriceSampleObserved],
    now: datetime,
) -> SessionSummary:
    if not samples:
        if now < window.opens_at + GRACE:
            raise AvanzaPending("OPENING_SAMPLE_NOT_DUE")
        raise AvanzaUnavailable("NO_AVANZA_HISTORY")
    expected = [
        s.scheduled_at for s in slots(window, samples[0].interval_seconds) if s.kind == "REGULAR"
    ]
    present = {s.scheduled_at for s in samples}
    missing = [s for s in expected if s + GRACE <= now and s not in present]
    return SessionSummary(
        asset_id=asset_id,
        session=window.session,
        currency=samples[0].currency,
        open=samples[0].price,
        high=max(s.price for s in samples),
        low=min(s.price for s in samples),
        close=samples[-1].price,
        provisional=now < window.closes_at + GRACE,
        complete=present == set(expected),
        observed_samples=len(samples),
        expected_samples=len(expected),
        missing_slots=missing,
        first_sample=provenance(samples[0]),
        last_sample=provenance(samples[-1]),
    )


def minute_values(samples: list[PriceSampleObserved], now: datetime) -> list[IntradayBar]:
    """Only completed minute buckets, available after the actual read; no gap filling."""
    result: dict[datetime, IntradayBar] = {}
    for sample in samples:
        evidence = provenance(sample)
        start = sample.observed_at.replace(second=0, microsecond=0)
        at = start
        while at < evidence.expires_at and at + MINUTE <= min(now, sample.closes_at):
            result[at] = IntradayBar(
                start=at,
                open=sample.price,
                high=sample.price,
                low=sample.price,
                close=sample.price,
                sample=evidence,
                carried_forward=at != start,
            )
            at += MINUTE
    return sorted(result.values(), key=lambda b: b.start)


class AvanzaReader:
    def __init__(self, pool: asyncpg.Pool, mappings_path: Path) -> None:
        self.pool, self.mappings_path = pool, mappings_path

    def listing(self, asset_id: AssetId) -> Listing:
        listing = next(
            (
                x
                for x in load_mappings(self.mappings_path).listings
                if x.asset_id == asset_id and x.enabled
            ),
            None,
        )
        if listing is None:
            raise AvanzaUnavailable("UNSUPPORTED_AVANZA_LISTING")
        return listing

    def window(self, asset_id: AssetId, day: date) -> Session:
        listing = self.listing(asset_id)
        local_noon = datetime.combine(
            day, datetime.min.time(), tzinfo=ZoneInfo(listing.timezone)
        ) + timedelta(hours=12)
        window = session_for(local_noon, listing.calendar_id, listing.timezone)
        if window.session != day:
            raise AvanzaPending("NON_TRADING_SESSION")
        return window

    async def samples(
        self,
        asset_id: AssetId,
        day: date,
        now: datetime,
    ) -> tuple[Session, list[PriceSampleObserved]]:
        window = self.window(asset_id, day)
        if not await self.pool.fetchval("SELECT to_regclass('market_data.price_samples')"):
            if now < window.opens_at + GRACE:
                raise AvanzaPending("SESSION_NOT_STARTED")
            raise AvanzaUnavailable("COLLECTOR_NOT_INITIALIZED")
        rows = await self.pool.fetch(
            "SELECT payload,instrument_id,exchange FROM market_data.price_samples "
            "WHERE asset_id=$1 AND session=$2 "
            "ORDER BY scheduled_at,observed_at",
            str(asset_id),
            day,
        )
        series = resolve(asset_id)
        listing = self.listing(asset_id)
        if series.is_adjusted:
            raise AvanzaUnavailable("ADJUSTED_PRICE_SERIES_UNSUPPORTED")
        if any(
            r["instrument_id"] != listing.instrument_id
            or r["exchange"] != listing.expected_exchange
            for r in rows
        ):
            raise AvanzaUnavailable("STORED_LISTING_MISMATCH")
        return window, validate_samples(
            [PriceSampleObserved.model_validate_json(r["payload"]) for r in rows],
            asset_id,
            window,
            series.currency,
            series.registry_version,
            now,
        )

    async def summary(self, asset_id: AssetId, day: date, now: datetime) -> SessionSummary:
        window, samples = await self.samples(asset_id, day, now)
        return summarize(asset_id, window, samples, now)

    async def close(self, asset_id: AssetId, day: date, now: datetime) -> CloseObservation:
        window = self.window(asset_id, day)
        if now < window.closes_at + GRACE:
            raise AvanzaPending("SESSION_STILL_OPEN_OR_FINALIZING")
        summary = await self.summary(asset_id, day, now)
        if not summary.complete:
            raise AvanzaUnavailable("INCOMPLETE_AVANZA_SESSION")
        return CloseObservation(
            session=day,
            close=summary.close,
            fetched_at=now,
            source="avanza",
            provider_symbol=self.listing(asset_id).instrument_id,
            price_kind=PriceKind.AVANZA_SAMPLED_CLOSE,
            is_adjusted=False,
            registry_version=resolve(asset_id).registry_version,
            sample=summary.last_sample,
        )

    async def minutes(
        self,
        asset_id: AssetId,
        day: date,
        now: datetime,
    ) -> list[IntradayBar]:
        window, samples = await self.samples(asset_id, day, now)
        summary = summarize(asset_id, window, samples, now)
        if summary.missing_slots:
            raise AvanzaUnavailable("MISSING_AVANZA_SLOT")
        return minute_values(samples, now)

    def recent_sessions(self, asset_id: AssetId, count: int, now: datetime) -> list[date]:
        import exchange_calendars as calendars  # type: ignore[import-untyped]

        listing = self.listing(asset_id)
        cal: Any = calendars.get_calendar(listing.calendar_id)
        day = cal.date_to_session(now.astimezone(cal.tz).date(), direction="previous")
        if now < cal.session_close(day).to_pydatetime() + GRACE:
            day = cal.previous_session(day)
        days = []
        for _ in range(count):
            days.append(day.date())
            day = cal.previous_session(day)
        return days


def fallback_reason(exc: Exception) -> str:
    # Exception messages from HTTP/database clients may contain credentials.
    return str(exc) if isinstance(exc, AvanzaUnavailable) else "AVANZA_READ_ERROR"


class AvanzaFirstPrices:
    def __init__(self, reader: AvanzaReader, finance: PriceAdapter) -> None:
        self.reader, self.finance = reader, finance

    async def get_pair(
        self,
        asset_id: AssetId,
        baseline: date,
        settlement: date,
        now: datetime,
    ) -> tuple[CloseObservation, CloseObservation]:
        # An open market is never a provider failure, even when baseline history is absent.
        try:
            if now < self.reader.window(asset_id, settlement).closes_at + GRACE:
                raise AvanzaPending("SESSION_STILL_OPEN_OR_FINALIZING")
        except AvanzaPending:
            raise
        except Exception:
            series = resolve(asset_id)
            if not is_session_complete(
                settlement,
                series.timezone,
                now=now,
                hour=series.session_completion_hour,
                minute=series.session_completion_minute,
            ):
                raise AvanzaPending("SESSION_STILL_OPEN") from None
        try:
            first = await self.reader.close(asset_id, baseline, now)
            last = await self.reader.close(asset_id, settlement, now)
            if (
                first.sample is None
                or last.sample is None
                or first.sample.mapping_version != last.sample.mapping_version
            ):
                raise AvanzaUnavailable("PAIR_MAPPING_CHANGED")
            return first, last
        except AvanzaPending:
            raise
        except Exception as exc:
            reason = fallback_reason(exc)
            logger.warning("avanza_finance_fallback", asset_id=str(asset_id), reason=reason)
        first = await self.finance.get_close(asset_id, baseline)
        last = await self.finance.get_close(asset_id, settlement)
        series = resolve(asset_id)
        if any(x.provider_symbol != series.provider_symbol for x in (first, last)):
            raise AvanzaUnavailable("FINANCE_FALLBACK_LISTING_MISMATCH")
        if (first.source, first.price_kind, first.is_adjusted, first.registry_version) != (
            last.source,
            last.price_kind,
            last.is_adjusted,
            last.registry_version,
        ):
            raise AvanzaUnavailable("FINANCE_FALLBACK_PAIR_MISMATCH")
        return (
            first.model_copy(update={"fallback_reason": reason}),
            last.model_copy(update={"fallback_reason": reason}),
        )

    async def recent(
        self,
        asset_id: AssetId,
        count: int,
        now: datetime,
    ) -> list[CloseObservation]:
        primary: dict[date, CloseObservation] = {}
        days: list[date] = []
        reason = "NO_AVANZA_HISTORY"
        try:
            days = self.reader.recent_sessions(asset_id, count, now)
            for day in days:
                try:
                    primary[day] = await self.reader.close(asset_id, day, now)
                except AvanzaPending:
                    continue
                except Exception as exc:
                    reason = fallback_reason(exc)
            if len(primary) == count:
                return [primary[day] for day in days]
        except Exception as exc:
            reason = fallback_reason(exc)
        # One provider-window request, never a burst of one request per historical day.
        series = resolve(asset_id)
        try:
            rows = await self.reader.pool.fetch(
                "SELECT session,close,provider_bar_time,fetched_at,source,provider_symbol,"
                "price_kind,is_adjusted,registry_version FROM market_data.close_observations "
                "WHERE asset_id=$1 AND registry_version=$2 AND provider_symbol=$3 "
                "ORDER BY session DESC LIMIT $4",
                str(asset_id),
                series.registry_version,
                series.provider_symbol,
                count,
            )
        except Exception:
            rows = []
        stored = {r["session"]: CloseObservation(**dict(r)) for r in rows}
        if not days or any(day not in primary and day not in stored for day in days):
            try:
                fetched = await self.finance.fetch_observations(
                    asset_id, now.astimezone(ZoneInfo(series.timezone)).date()
                )
                for value in fetched:
                    if value.provider_symbol == series.provider_symbol:
                        stored.setdefault(value.session, value)
            except Exception:
                if not stored and not primary:
                    raise
        values = {
            day: value.model_copy(update={"fallback_reason": reason})
            for day, value in stored.items()
            if not days or day in days
        }
        values.update(primary)
        return sorted(values.values(), key=lambda v: v.session, reverse=True)[:count]
