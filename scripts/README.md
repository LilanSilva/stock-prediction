# scripts — Manual scripts

Scripts a user or developer runs **by hand**. Nothing here is invoked by a running service; the
services' own scheduled jobs are in their `app.py` and documented in each SRS section 7.

## Environment setup

| Script | Platform |
|---|---|
| [setup-venv.ps1](setup-venv.ps1) | Windows (PowerShell) |
| [setup-venv.sh](setup-venv.sh) | macOS/Linux (bash) |

Run once when setting up the repository. Both create `.venv/` at the repository root, install the
hash-verified dependencies, and install the local `shared` package editable. Safe to re-run to sync
after the lockfile changes.

**Invocation and full setup notes are in the repository
[README](../README.md#first-time-setup)** — including the manual equivalent if you prefer not to run a
script.

## Asset registry

Run these after **any** edit to `assets.json`. Structural validation alone cannot catch a typo'd
ticker — the registry would load, the asset would predict, and scoring would then stall silently on
`PriceNotYetAvailable`.

| Script | Purpose |
|---|---|
| [validate-assets.py](validate-assets.py) | Validate registry structure, then probe every `provider_symbol` live and cross-check the provider's reported currency and timezone |
| [generate-asset-seed.py](generate-asset-seed.py) | Regenerate the Neo4j asset/group seed Cypher from the registry |

```bash
python scripts/validate-assets.py              # structure + live provider probe
python scripts/validate-assets.py --offline    # structure only (CI)
python scripts/generate-asset-seed.py          # regenerate the seed
python scripts/generate-asset-seed.py --check  # verify the seed matches the registry
```

Registry rules and field definitions: [REF-02](../requirements/REF-02-asset-registry.md).

## Operational reporting

These require the local Docker stack to be running. They read credentials from `infra/.env` and query
Postgres and Neo4j via `docker exec`. Each writes a timestamped HTML file next to itself.

| Script | Produces |
|---|---|
| [query-daily-activity.ps1](query-daily-activity.ps1) | `daily-activity-<date>.html` — news retrieved and predictions with their verified results |
| [debug-pipeline.ps1](debug-pipeline.ps1) | `debug-pipeline-<date>.html` — diagnostic report: ingestion/cleansing gaps, articles classified `OTHER`, predictions and verification state |
| [export-cleansing-audit.ps1](export-cleansing-audit.ps1) | `cleansing-audit-<date>.json` — each cleansing cluster with its raw articles (title, body excerpt, NLP extraction) for manual LLM verification of event-group classification |

```powershell
powershell -File scripts\query-daily-activity.ps1 -Date 2026-08-06
powershell -File scripts\debug-pipeline.ps1 -Date 2026-08-06
powershell -File scripts\export-cleansing-audit.ps1 -Date 2026-08-06
powershell -File scripts\export-cleansing-audit.ps1 -Date 2026-08-06 -OutputPath C:\tmp\audit.json
```

All date filtering is UTC, matching the containers' server time.

The generated `*.html` reports are point-in-time output, not documentation — they accumulate in this
folder and are safe to delete.

## Demo

| Script | Purpose |
|---|---|
| [demo_publish_event.py](demo_publish_event.py) | Publish one `EventDetected` (war de-escalation affecting oil transport) to `feed.events`, to exercise the prediction path by hand |

Requires the stack to be running. Useful for watching a `RESOLUTION`-polarity event flow through
Prediction without waiting for real news.
