# T02: Stooq Fallback Adapter

> **POC-6 replacement scope (2026-07-13):** This is now a validation spike, not an implementation commitment. The live canary returned non-CSV responses for `^gold` and `^oil`. Prove stable programmatic access and equivalent instrument, currency, session, adjustment, and rollover semantics for both assets before implementing or enabling fallback. Until then the registry fallback is `null`, and all transparent-fallback requirements below are non-authoritative.

> **Spike continuation (2026-07-28) — FAIL, fallback stays `null`:** Re-ran the live Stooq CSV probe against the same endpoint (`https://stooq.com/q/d/l/?s=<symbol>&d1=...&i=d`) across multiple candidate symbol conventions — GOLD (`^gold`, `gc.f`, `xauusd`) and BRENT_OIL (`^oil`, `cb.f`, `lco`, `cl.f`). Every candidate returned **HTTP 200 with an HTML JavaScript anti-bot challenge** (`<!DOCTYPE html>… noindex,nofollow …(async()=>{…TextEncoder…}`, 796 bytes), not CSV. Retrying with a realistic browser `User-Agent` produced the same challenge. This is worse than the original finding: the endpoint no longer serves CSV to any plain programmatic client (including `pandas-datareader`'s Stooq reader, which issues the same GET), so it is an **access-layer failure**, not a symbol-mapping problem — and semantic equivalence cannot even be assessed because no data is returned. **Decision:** Stooq is not a viable programmatic fallback; the frozen registry policy `fallback: null` stands. E05 proceeds **yfinance / Yahoo-chart only**. Revisit only with a JS-capable fetch (headless browser — heavy/fragile/ToS risk) or a different keyed provider, neither of which is POC scope.

## Context

The Market Data Service uses yfinance as its primary price source (T01). When yfinance raises `AdapterUnavailableError` after 3 retries, the service must transparently fall back to **Stooq** via `pandas-datareader`. This task implements that fallback adapter at `src/services/market-data/adapters/stooq_adapter.py`. It exposes the same `get_ohlc(symbol, date)` interface as the yfinance adapter, so the caller (S02-T01) needs no special-case logic beyond catching the primary adapter's error.

## Background

Stooq is a free financial data provider supported by `pandas-datareader`. It uses a **different symbol convention** from Yahoo Finance:

| Asset | Yahoo Finance | Stooq |
|---|---|---|
| Gold (spot/futures) | `GC=F` | `^GOLD` |
| Brent crude | `BZ=F` | `^OIL` |
| USD index | `DX-Y.NYB` | `^USD` |
| S&P 500 ETF | `SPY` | `SPY.US` |
| Swedish equity | `ERIC-B.ST` | `ERIC-B.PL` (Warsaw-listed cross) |
| OMXS30 | `OMXS30=F` | `^OMX` |

The symbol mapping must be maintained as a dictionary in the adapter. Unknown symbols that do not appear in the map should fall back to a heuristic: strip the `.ST` suffix and append `.PL`.

`pandas_datareader.data.DataReader(symbol, 'stooq', start, end)` is synchronous. Wrap it in `ThreadPoolExecutor` exactly as done in the yfinance adapter.

Log which adapter was used on every successful fetch — this is a hard requirement from the service spec so operators can monitor fallback frequency.

## Inputs

- `symbol: str` — Yahoo Finance-format ticker. The adapter internally maps it to Stooq format.
- `date: datetime.date` — the target trading date (UTC calendar date)
- No queue messages; called by the request handler after yfinance failure

## Outputs

Returns the same `OHLCResult` Pydantic model as the yfinance adapter (defined in `src/shared/schemas/prices.py`), with `source == 'stooq'`.

Raises `AdapterUnavailableError` (from `src/services/market-data/exceptions.py`) if Stooq also fails after 3 retries or returns no data.

## Technical Requirements

### File locations
- `src/services/market-data/adapters/stooq_adapter.py`
- `src/services/market-data/adapters/symbol_map.py` — centralised symbol mapping dict
- `src/services/market-data/tests/test_stooq_adapter.py`

### Libraries
- `pandas-datareader>=0.10.0` — `DataReader(..., 'stooq', ...)`
- `tenacity>=8.2` — same retry config as yfinance adapter (3 attempts, exponential back-off)
- `pandas>=2.0`
- `asyncio` + `concurrent.futures.ThreadPoolExecutor`

### Symbol mapping (in `symbol_map.py`)
```python
YFINANCE_TO_STOOQ: dict[str, str] = {
    "GC=F":       "^GOLD",
    "BZ=F":       "^OIL",
    "DX-Y.NYB":   "^USD",
    "SPY":        "SPY.US",
    "OMXS30=F":   "^OMX",
    # Add more as needed
}

def to_stooq_symbol(yf_symbol: str) -> str:
    if yf_symbol in YFINANCE_TO_STOOQ:
        return YFINANCE_TO_STOOQ[yf_symbol]
    # Heuristic: ERIC-B.ST → ERIC-B.PL
    if yf_symbol.endswith(".ST"):
        return yf_symbol.replace(".ST", ".PL")
    # US equities: AAPL → AAPL.US
    return f"{yf_symbol}.US"
```

### Core function signature
```python
async def get_ohlc(symbol: str, date: datetime.date) -> OHLCResult:
    stooq_symbol = to_stooq_symbol(symbol)
    df = await _fetch_async(stooq_symbol, date)
    result = _parse_row(symbol, df)  # symbol preserved as original yf symbol
    logger.info("price_fetch", adapter="stooq", symbol=symbol, date=str(date))
    return result
```

### Retry configuration
Identical to yfinance adapter — `@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=8))`.

### Date window
Fetch a 7-calendar-day window ending on `date + timedelta(days=1)`. Stooq returns data sorted newest-first; take `.iloc[0]` for the most recent row on or before `date`.

### Logging requirement
Every successful fetch must emit a structured log entry:
```python
logger.info("price_fetch_success", adapter="stooq", original_symbol=symbol, stooq_symbol=stooq_symbol, date=str(actual_date))
```
Every fallback activation (i.e. every time this adapter is called rather than yfinance) must emit:
```python
logger.warning("yfinance_fallback_activated", symbol=symbol, date=str(date))
```
The warning should be emitted by the **caller** (the orchestrator function in S02), not inside this adapter. This adapter only logs its own success.

## Acceptance Criteria

1. `get_ohlc('GC=F', <recent_weekday>)` returns an `OHLCResult` with `source == 'stooq'` and all OHLC fields as non-zero positive floats.
2. `to_stooq_symbol('GC=F')` returns `'^GOLD'`.
3. `to_stooq_symbol('ERIC-B.ST')` returns `'ERIC-B.PL'`.
4. `to_stooq_symbol('AAPL')` returns `'AAPL.US'` (heuristic fallback for unmapped US equity).
5. `to_stooq_symbol('SPY')` returns `'SPY.US'` (explicit mapping takes precedence over heuristic).
6. When `pandas_datareader.data.DataReader` is monkeypatched to raise `Exception`, `get_ohlc` raises `AdapterUnavailableError` after exactly 3 attempts.
7. Calling `get_ohlc('BZ=F', <saturday_date>)` returns the most recent preceding trading-day result (same weekend-gap handling as the yfinance adapter).
8. The returned `OHLCResult.symbol` field contains the **original Yahoo Finance symbol** passed in, not the Stooq symbol.
9. All type annotations pass `mypy --strict`.
10. All tests pass under `pytest -v`.

## Implementation Notes

- **Stooq column order:** `pandas_datareader` with Stooq returns columns `['Open', 'High', 'Low', 'Close', 'Volume']` with the index as the date. Rows are sorted **descending** (newest first), unlike yfinance which is ascending. Always use `.iloc[0]` for the most recent row.
- **Missing volume:** Stooq sometimes returns `NaN` for `volume` on index/commodity symbols. Cast to `int` with a default of `0` if NaN: `int(row.get('Volume', 0) or 0)`.
- **Rate limiting:** Stooq does not publish rate limits but will block repeated rapid requests. Add `asyncio.sleep(0.5)` between retries (inside the `wait_exponential` config is fine; the `min=2` already handles this).
- **Swedish tickers (.PL):** Stooq hosts cross-listed Polish copies of Swedish equities. Data quality is generally good but volume may differ from the Stockholm exchange. This is acceptable for close-price verification.
- **Do not** import the Stooq adapter inside the yfinance adapter — keep adapters independent. The orchestration/fallback logic lives in `src/services/market-data/price_fetcher.py` (implemented in S02-T01).
- **Symbol map maintenance:** When new assets are added to the prediction universe, `YFINANCE_TO_STOOQ` in `symbol_map.py` is the single place to update. Document this in a comment.
- Test with real network calls disabled by default (`@pytest.mark.vcr` or `responses` library mocking). Only integration tests tagged `@pytest.mark.integration` should make real HTTP calls.

## Definition of Done

- [ ] `src/services/market-data/adapters/stooq_adapter.py` exists with `async def get_ohlc(symbol, date) -> OHLCResult`
- [ ] `src/services/market-data/adapters/symbol_map.py` exists with `YFINANCE_TO_STOOQ` dict and `to_stooq_symbol()` function
- [ ] Symbol mapping covers: `GC=F`, `BZ=F`, `DX-Y.NYB`, `SPY`, `OMXS30=F`
- [ ] Heuristic handles `.ST` suffix and unmapped US tickers
- [ ] `source` field in returned `OHLCResult` is always `'stooq'`
- [ ] Retry logic uses `tenacity` with 3 attempts and exponential back-off
- [ ] Weekend/holiday gap handling returns last available trading day
- [ ] Unit tests in `tests/test_stooq_adapter.py` cover: happy path, symbol mapping, retry exhaustion, weekend date, NaN volume handling
- [ ] `ruff check src/services/market-data/adapters/stooq_adapter.py` passes
- [ ] `mypy src/services/market-data/adapters/stooq_adapter.py` passes
- [ ] `pytest src/services/market-data/tests/test_stooq_adapter.py -v` all tests green
