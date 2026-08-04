"""Tests for the Yahoo adapter: parallel-array parsing, null gaps, unsettled-bar exclusion,
local-market session mapping, identity validation, and the browser User-Agent.

Fixture-based (no network): the shapes here mirror a real Yahoo chart response captured from
``SAAB-B.ST``, including a ``null`` close and a still-forming final bar.
"""

from __future__ import annotations

import copy
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from shared.reference import REGISTRY_VERSION, resolve
from shared.schemas.messages import AssetId, PriceKind

from market_data.adapters.yahoo import (
    _BROWSER_USER_AGENT,
    YahooAdapter,
    _valid_close,
)
from market_data.exceptions import (
    AdapterUnavailableError,
    InvalidObservationError,
    PriceNotYetAvailableError,
)
from tests.conftest import make_client

_SAAB = AssetId.SAAB_B_STO

# Stockholm bars are stamped 07:00 UTC (09:00 local market open).
_MON = datetime(2026, 7, 27, 7, 0, tzinfo=UTC)
_TUE = datetime(2026, 7, 28, 7, 0, tzinfo=UTC)
_WED = datetime(2026, 7, 29, 7, 0, tzinfo=UTC)


def _chart(
    *,
    timestamps: list[int] | None = None,
    closes: list[object] | None = None,
    currency: str = "SEK",
    timezone_name: str = "Europe/Stockholm",
) -> dict[str, Any]:
    return {
        "chart": {
            "error": None,
            "result": [
                {
                    "meta": {
                        "currency": currency,
                        "symbol": "SAAB-B.ST",
                        "exchangeName": "STO",
                        "exchangeTimezoneName": timezone_name,
                    },
                    "timestamp": timestamps
                    if timestamps is not None
                    else [int(_MON.timestamp()), int(_TUE.timestamp()), int(_WED.timestamp())],
                    "indicators": {
                        "quote": [
                            {
                                "close": closes
                                if closes is not None
                                else [615.40, 599.60, 591.30]
                            }
                        ]
                    },
                }
            ],
        }
    }


def _adapter_returning(payload: dict[str, Any], status: int = 200) -> YahooAdapter:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload)

    return YahooAdapter(make_client(handler))


# --- happy path ---------------------------------------------------------------------------------


async def test_get_close_returns_validated_observation() -> None:
    obs = await _adapter_returning(_chart()).get_close(_SAAB, date(2026, 7, 29))
    assert obs.session == date(2026, 7, 29)
    assert obs.close == Decimal("591.3")
    assert obs.provider_symbol == "SAAB-B.ST"
    assert obs.price_kind == PriceKind.PROVIDER_DAILY_CLOSE
    assert obs.is_adjusted is False
    assert obs.source == "yahoo"
    assert obs.registry_version == REGISTRY_VERSION


async def test_parallel_arrays_are_zipped_into_observations() -> None:
    obs = await _adapter_returning(_chart()).fetch_observations(_SAAB, date(2026, 7, 29))
    assert [o.session for o in obs] == [
        date(2026, 7, 27),
        date(2026, 7, 28),
        date(2026, 7, 29),
    ]


async def test_bar_maps_to_the_local_market_session_not_utc() -> None:
    # A bar stamped 22:30 UTC on the 27th is already 00:30 on the 28th in Stockholm. Mapping it to
    # the UTC date would shift every Swedish close by a day.
    late = datetime(2026, 7, 27, 22, 30, tzinfo=UTC)
    payload = _chart(timestamps=[int(late.timestamp())], closes=[600.0])
    obs = await _adapter_returning(payload).fetch_observations(_SAAB, date(2026, 7, 28))
    assert [o.session for o in obs] == [date(2026, 7, 28)]


async def test_sends_a_browser_user_agent() -> None:
    # Load-bearing: Yahoo answers a non-browser agent with HTTP 429. This is the actual cause of the
    # 429s that retired the endpoint in POC-7.
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["ua"] = request.headers.get("user-agent", "")
        return httpx.Response(200, json=_chart())

    await YahooAdapter(make_client(handler)).fetch_observations(_SAAB, date(2026, 7, 29))
    assert seen["ua"] == _BROWSER_USER_AGENT
    assert "Mozilla" in seen["ua"]
    assert "feed-analyzer" not in seen["ua"]


# --- gaps and unsettled bars ---------------------------------------------------------------------


async def test_null_close_is_skipped_not_zero_filled() -> None:
    # A null means "no trade". Treating it as a price would corrupt the close-to-close return.
    payload = _chart(closes=[615.40, None, 591.30])
    obs = await _adapter_returning(payload).fetch_observations(_SAAB, date(2026, 7, 29))
    assert [o.session for o in obs] == [date(2026, 7, 27), date(2026, 7, 29)]
    assert all(o.close > 0 for o in obs)


async def test_todays_still_forming_bar_is_excluded() -> None:
    # Yahoo has no isOpen flag, so an unsettled last tick must be excluded by the market's own
    # closing clock rather than stored as a settled daily close.
    now = datetime.now(UTC)
    today_bar = datetime.combine(now.date(), datetime.min.time(), tzinfo=UTC) + timedelta(hours=7)
    payload = _chart(timestamps=[int(_WED.timestamp()), int(today_bar.timestamp())],
                     closes=[591.30, 626.30])
    obs = await _adapter_returning(payload).fetch_observations(_SAAB, date(2026, 7, 29))
    assert now.date() not in [o.session for o in obs]

    with pytest.raises(PriceNotYetAvailableError):
        await _adapter_returning(payload).get_close(_SAAB, now.date())


@pytest.mark.parametrize("bad_close", [0, -1.5, "not-a-number", True, {}])
async def test_non_positive_or_unparseable_closes_are_skipped(bad_close: object) -> None:
    payload = _chart(closes=[bad_close, 599.60, 591.30])
    obs = await _adapter_returning(payload).fetch_observations(_SAAB, date(2026, 7, 29))
    assert date(2026, 7, 27) not in [o.session for o in obs]
    assert len(obs) == 2


@pytest.mark.parametrize("bad_close", [float("inf"), float("-inf"), float("nan")])
def test_non_finite_closes_are_rejected(bad_close: float) -> None:
    # Tested directly: non-finite floats cannot round-trip through JSON, so they cannot reach the
    # adapter via a fixture payload.
    assert _valid_close(bad_close) is None


async def test_missing_session_raises_price_not_yet_available() -> None:
    with pytest.raises(PriceNotYetAvailableError):
        await _adapter_returning(_chart()).get_close(_SAAB, date(2026, 7, 24))


# --- identity validation -------------------------------------------------------------------------


async def test_currency_mismatch_is_terminal() -> None:
    # Guards against the provider silently returning a different listing for a symbol.
    payload = _chart(currency="USD")
    with pytest.raises(InvalidObservationError, match="currency"):
        await _adapter_returning(payload).fetch_observations(_SAAB, date(2026, 7, 29))


async def test_timezone_mismatch_is_terminal() -> None:
    payload = _chart(timezone_name="America/New_York")
    with pytest.raises(InvalidObservationError, match="timezone"):
        await _adapter_returning(payload).fetch_observations(_SAAB, date(2026, 7, 29))


async def test_registry_currency_and_timezone_are_what_we_validate_against() -> None:
    series = resolve(_SAAB)
    assert series.currency == "SEK"
    assert series.timezone == "Europe/Stockholm"


# --- malformed payloads --------------------------------------------------------------------------


async def test_in_band_error_is_terminal() -> None:
    # Yahoo reports an unknown symbol with HTTP 200 and a populated error block.
    payload: dict[str, Any] = {"chart": {"error": {"code": "Not Found"}, "result": None}}
    with pytest.raises(InvalidObservationError, match="rejected"):
        await _adapter_returning(payload).fetch_observations(_SAAB, date(2026, 7, 29))


async def test_empty_result_is_not_yet_available() -> None:
    payload: dict[str, Any] = {"chart": {"error": None, "result": []}}
    with pytest.raises(PriceNotYetAvailableError):
        await _adapter_returning(payload).fetch_observations(_SAAB, date(2026, 7, 29))


async def test_length_mismatch_between_timestamps_and_closes_is_terminal() -> None:
    payload = _chart(timestamps=[int(_MON.timestamp())], closes=[615.40, 599.60])
    with pytest.raises(InvalidObservationError, match="length mismatch"):
        await _adapter_returning(payload).fetch_observations(_SAAB, date(2026, 7, 29))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.pop("chart"),
        lambda p: p["chart"]["result"][0].pop("meta"),
        lambda p: p["chart"]["result"][0].pop("timestamp"),
        lambda p: p["chart"]["result"][0].pop("indicators"),
        lambda p: p["chart"]["result"][0]["indicators"].pop("quote"),
        lambda p: p["chart"]["result"][0]["indicators"]["quote"][0].pop("close"),
    ],
)
async def test_malformed_structure_is_terminal(mutate: Any) -> None:
    payload = copy.deepcopy(_chart())
    mutate(payload)
    with pytest.raises(InvalidObservationError):
        await _adapter_returning(payload).fetch_observations(_SAAB, date(2026, 7, 29))


async def test_unparseable_timestamp_is_terminal() -> None:
    payload = _chart(timestamps=["yesterday"], closes=[600.0])  # type: ignore[list-item]
    with pytest.raises(InvalidObservationError, match="timestamp"):
        await _adapter_returning(payload).fetch_observations(_SAAB, date(2026, 7, 29))


# --- transport -----------------------------------------------------------------------------------


async def test_http_error_is_transient() -> None:
    # 429 in particular must be retried, not dead-lettered: the request would succeed later.
    for status in (429, 500, 503):
        with pytest.raises(AdapterUnavailableError):
            await _adapter_returning(_chart(), status=status).fetch_observations(
                _SAAB, date(2026, 7, 29)
            )


async def test_transport_failure_is_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    with pytest.raises(AdapterUnavailableError):
        await YahooAdapter(make_client(handler)).fetch_observations(_SAAB, date(2026, 7, 29))
