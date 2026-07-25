# T01: yfinance Adapter

> **POC-6 replacement requirements (2026-07-13):** `GC=F` and `BZ=F` returned daily data, but this adapter is not approved until P06/T03 freezes the series policy. Fetch raw provider daily closes (`auto_adjust=False`), preserve provider exchange/timezone, bar timestamp, fetch time, instrument, currency, and registry version, and apply the pre-declared rollover policy. `BZ=F` metadata currently conflicts with the intended ICE-Europe calendar. Never label Yahoo data an official settlement. Conflicting automatic-approval and `auto_adjust=True` details below are non-authoritative.

## Context

The Market Data Service fetches real OHLC price data for assets that the Prediction Service has generated predictions on. This task implements the **primary price-data adapter** using the `yfinance` library. The adapter is a pure data-access module (`services/market-data/adapters/yfinance_adapter.py`) that the price-request handler (S02-T01) will call. It is the first point of contact with external market data and must be robust: retrying on transient failures and gracefully handling non-trading days.

## Background

`yfinance` wraps Yahoo Finance's unofficial API. It is synchronous by default, so we run it in a thread-pool executor to keep the service non-blocking. Key considerations:

- **Symbol format:** Yahoo Finance uses its own ticker conventions. Gold futures = `GC=F`, Brent crude = `BZ=F`, USD index = `DX-Y.NYB`, Swedish equities use the `.ST` suffix (e.g. `ERIC-B.ST`).
- **Weekend / holiday gaps:** `yf.download` returns no row for non-trading days. The adapter must detect this and return the most recent available trading-day close.
- **Retry strategy:** Yahoo Finance rate-limits aggressively. Use `tenacity` with exponential back-off (3 attempts, wait 2s/4s/8s). After 3 failures raise `AdapterUnavailableError` so the caller can switch to Stooq.
- **Date range for a single day:** To fetch one day's OHLC, download a 5-calendar-day window ending on `date + 1 business day` and take the last row. This handles same-day data not yet available on the API.

## Inputs

- `symbol: str` — Yahoo Finance ticker (e.g. `'GC=F'`, `'ERIC-B.ST'`, `'SPY'`)
- `date: datetime.date` — the target trading date (UTC calendar date)
- No queue messages; this is a synchronous helper called by the request handler

## Outputs

Returns an `OHLCResult` Pydantic model (defined in `src/shared/schemas/prices.py`):

```python
class OHLCResult(BaseModel):
    symbol: str
    date: datetime.date          # actual trading date data was sourced from
    open: float
    high: float
    low: float
    close: float
    volume: int
    source: Literal["yfinance", "stooq"]
```

Raises `AdapterUnavailableError` (custom exception in `services/market-data/exceptions.py`) after exhausting retries.

## Technical Requirements

### File locations
- `services/market-data/adapters/__init__.py`
- `services/market-data/adapters/yfinance_adapter.py`
- `services/market-data/exceptions.py`
- `src/shared/schemas/prices.py` (add `OHLCResult` if not present)
- `services/market-data/tests/test_yfinance_adapter.py`

### Libraries
- `yfinance>=0.2.40` — primary data source
- `tenacity>=8.2` — retry decorator
- `pandas>=2.0` — yfinance returns DataFrames
- `asyncio` + `concurrent.futures.ThreadPoolExecutor` — wrap sync yfinance calls

### Core function signature
```python
async def get_ohlc(symbol: str, date: datetime.date) -> OHLCResult:
    ...
```

### Retry configuration (tenacity)
```python
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=8),
    reraise=False,
    retry=retry_if_exception_type(Exception),
)
def _download_sync(symbol: str, start: str, end: str) -> pd.DataFrame:
    return yf.download(symbol, start=start, end=end, auto_adjust=True, progress=False)
```

After 3 failures, catch `RetryError` and raise `AdapterUnavailableError(symbol)`.

### Weekend / holiday handling
- Download a 7-calendar-day window ending the day after `date`
- If the DataFrame is empty, extend the window to 14 days (to handle holiday clusters)
- If still empty after 14 days, raise `AdapterUnavailableError`
- Return the **last available row** (most recent trading day on or before `date`)

### Supported asset symbols
| Asset class | Example symbols |
|---|---|
| Gold futures | `GC=F` |
| Brent crude futures | `BZ=F` |
| USD index | `DX-Y.NYB` |
| US broad equity | `SPY` |
| Individual US equity | Any NASDAQ/NYSE ticker |
| Swedish equity | `ERIC-B.ST`, `VOLV-B.ST`, `OMXS30=F` |

### Async execution
```python
import asyncio
from concurrent.futures import ThreadPoolExecutor

_executor = ThreadPoolExecutor(max_workers=4)

async def get_ohlc(symbol: str, date: datetime.date) -> OHLCResult:
    loop = asyncio.get_event_loop()
    df = await loop.run_in_executor(_executor, _fetch_with_retry, symbol, date)
    return _parse_row(symbol, df)
```

## Acceptance Criteria

1. `get_ohlc('GC=F', date.today())` returns an `OHLCResult` with `source == 'yfinance'` and all four OHLC fields as non-zero positive floats.
2. `get_ohlc('ERIC-B.ST', <recent_weekday>)` returns a valid result with the `.ST` ticker preserved in the returned `symbol` field.
3. When `yf.download` is monkeypatched to raise `Exception` on every call, `get_ohlc` raises `AdapterUnavailableError` after exactly 3 attempts (verified via `tenacity` retry counter or call-count mock).
4. Calling `get_ohlc('SPY', <saturday_date>)` returns a result whose `date` field is the most recent preceding Friday (or the last trading day before any holiday).
5. Calling `get_ohlc` is non-blocking: verified by running it inside an `asyncio.get_event_loop().run_until_complete()` and confirming it does not block the event loop for more than 1 second in unit tests using `asyncio.wait_for`.
6. `OHLCResult.source` is always `'yfinance'` for results from this adapter.
7. All functions have full type annotations and pass `mypy --strict`.
8. All tests pass under `pytest -v`.

## Implementation Notes

- **`auto_adjust=True`** in `yf.download` applies dividend/split adjustments. Always use this for consistency.
- **Column naming:** yfinance column headers may be multi-level (ticker, field) when downloading multiple tickers. For single-ticker downloads the columns are flat: `Open`, `High`, `Low`, `Close`, `Volume`. Access with `.iloc[-1]` after confirming the DataFrame is non-empty.
- **Thread safety:** `yfinance` internally uses `requests.Session`. The `ThreadPoolExecutor` with `max_workers=4` is safe; do not share a single session across threads.
- **Logging:** Use the shared structured logger. Log at `DEBUG` for every download attempt, `WARNING` on each retry, and `ERROR` when raising `AdapterUnavailableError`. Always include `symbol` and `date` in the log context.
- **Do not** cache inside this adapter — caching is handled at the service layer (S02-T02).
- **`progress=False`** suppresses yfinance's tqdm progress bar in production logs.
- yfinance sometimes returns stale data for futures (`GC=F`) around roll dates. This is acceptable — the adapter should not attempt to detect or correct roll adjustments.

## Definition of Done

- [ ] `services/market-data/adapters/yfinance_adapter.py` exists with `async def get_ohlc(symbol, date) -> OHLCResult`
- [ ] `src/shared/schemas/prices.py` contains `OHLCResult` Pydantic model
- [ ] `services/market-data/exceptions.py` contains `AdapterUnavailableError`
- [ ] Retry logic uses `tenacity` with 3 attempts and exponential back-off
- [ ] Weekend/holiday gap handling returns last available trading day
- [ ] Unit tests in `tests/test_yfinance_adapter.py` cover: happy path, retry exhaustion, weekend date, Swedish `.ST` ticker
- [ ] `ruff check services/market-data/adapters/yfinance_adapter.py` passes with no errors
- [ ] `mypy services/market-data/adapters/yfinance_adapter.py` passes with no errors
- [ ] `pytest services/market-data/tests/test_yfinance_adapter.py -v` all tests green
