# POC-6 Executable Gate Harness

This standard-library harness runs the zero-token prerequisite gates and the completed P06 controlled rerun for POC-6.

It checks:

- whether the current RSS sources yield enough reproducible, distinct 60-minute conflict contexts;
- whether Gold and Brent daily price series are available and consistent with the canonical calendar policy;
- whether the Stooq fallback returns usable CSV data;
- whether a futures-roll policy has been declared;
- whether all future LLM attempts can be counted against one hard budget.

Run tests:

```powershell
Set-Location src/poc/poc6
python -m unittest -v
```

Run the live zero-token gates:

```powershell
python poc6.py --output results/latest.json
```

Start or resume the point-in-time RSS corpus:

```powershell
python collector.py --output data/raw/articles.jsonl --summary results/collection-latest.json
```

The collector stores one JSONL row per canonical URL: source, title, original and UTC publication times, UTC acquisition time, language, and hashes. It deliberately stores no article body and never reads price outcomes. Re-running it is duplicate-safe, and the JSONL file is an offline replay fixture. Publisher terms continue to govern the four source feeds listed in `poc6.py`; GDELT remains optional and is not enabled until its throttling/caching behavior is implemented.

The harness intentionally does not call Bedrock unless the data and market gates pass. A `REVISE` result is the POC-6 early-stop outcome, not a runtime failure.

Run the completed P06 controlled rerun:

```powershell
python complete_p06.py
```

The completed 2026-07-13 run produced `STOP` with 40 Bedrock calls, 10,762 input tokens, 3,357 output tokens, zero errors, and USD 0.082641 list-price cost. KG-plus-LLM did not beat graph-only on the frozen 30-context sample.
