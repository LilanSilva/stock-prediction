# S01 - Price Data Adapters

> **POC-7 provider migration (2026-07-30): superseded.** Both adapters described below (yfinance/Yahoo primary + Stooq fallback) are retired. Yahoo rate-limited this host's IP (`HTTP 429`), so the sole implemented adapter is now `BiquoteAdapter` (`src/services/market-data/market_data/adapters/biquote.py`) against **biquote.io** under policy `biquote-reference-v1` (`GOLD`→`XAUUSD`, `BRENT_OIL`→`UKOIL`). There is no fallback adapter (`fallback=null`). biquote's JSON has no `meta` block (no metadata validation), stamps daily bars at UTC midnight (session = bar date), and flags the unsettled day with `isOpen=true` (excluded). See the epic README banner and [backlog/POC/poc-7-biquote-price-source.md](../../POC/poc-7-biquote-price-source.md). The T01/T02 task docs below are retained for history and are non-authoritative where they conflict.

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
