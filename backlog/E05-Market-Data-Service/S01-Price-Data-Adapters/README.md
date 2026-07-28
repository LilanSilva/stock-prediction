# S01 - Price Data Adapters

## Overview

This story builds the two price-data adapters that provide OHLC data to the Market Data Service. Both adapters expose the same interface — `get_ohlc(symbol: str, date: date) -> OHLCResult` — so the rest of the service can swap between them transparently.

- **T01** implements the yfinance adapter (primary source)
- **T02** implements the Stooq fallback adapter via pandas-datareader

Together they form the data-access layer consumed by S02's price request handler.

## Tasks

| Task | Description |
|---|---|
| [T01 - yfinance adapter](T01-yfinance-adapter.md) | Async wrapper around yfinance with retry logic and weekend/holiday handling |
| [T02 - Stooq fallback adapter](T02-stooq-fallback-adapter.md) | pandas-datareader Stooq adapter with symbol mapping and logging |

## Dependencies

- `src/shared/` package must be initialised (Pydantic base models, logging setup)
- Python packages available: `yfinance>=0.2`, `pandas-datareader>=0.10`, `tenacity>=8.0`
- No queue or database dependencies — these are pure data-access modules

## How to Test End-to-End

1. Start a Python REPL or run the adapter smoke-test script:
   ```bash
   cd src/services/market-data
   python -m pytest tests/test_adapters.py -v
   ```
2. Verify `get_ohlc('GC=F', date.today())` returns a dict with non-null `close`.
3. Simulate yfinance failure by monkeypatching `yf.download` to raise an exception; confirm Stooq is called and the log message contains `stooq`.
4. Request a Saturday date; confirm the returned `date` field is the preceding Friday.
