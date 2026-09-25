# src — System code

All system code lives here: the shared library, the running services, and the completed POC harnesses.

**The code is the source of truth.** Where code and a specification disagree, the code is right and
the specification is a defect. Specifications are in [requirements/](../requirements/README.md) —
read the SRS for a service before changing it.

## Folders

| Folder | Contains | Specification |
|---|---|---|
| [shared/](shared/) | Installable `shared` package every service imports: message schemas, messaging client, LLM gateway, graph client, session calendar, asset registry, structured logging, Unicode text cleanup | [SRS-01](../requirements/SRS-01-shared-foundation.md) |
| [services/](services/) | The seven services, one folder each | one SRS each, see below |
| [poc/](poc/) | Completed proof-of-concept harnesses — standalone, not part of the running system | [backlog/POC/](../backlog/POC/README.md) |

## Services

Each service folder has the same shape: a `Dockerfile`, a `pyproject.toml`, the package itself, and a
`tests/` directory.

| Service | Postgres schema | Specification |
|---|---|---|
| [ingestion/](services/ingestion/) | `ingestion` | [SRS-02](../requirements/SRS-02-ingestion.md) |
| [cleansing/](services/cleansing/) | `cleansing` | [SRS-03](../requirements/SRS-03-cleansing.md) |
| [prediction/](services/prediction/README.md) | `prediction` | [SRS-04](../requirements/SRS-04-prediction.md) |
| [market-data/](services/market-data/) | `market_data` | [SRS-05](../requirements/SRS-05-market-data.md) |
| [verification/](services/verification/) | `verification` | [SRS-06](../requirements/SRS-06-verification.md) |
| [credibility/](services/credibility/) | `credibility` | [SRS-07](../requirements/SRS-07-credibility.md) |
| [notification/](services/notification/) | none — file-based recipients | [SRS-10](../requirements/SRS-10-notification.md) |

A service writes **only** the schema it owns. Cross-service data moves by message, never by writing
another service's tables.

## Common module layout

Services follow the same internal convention, so a file name tells you what it does:

| File | Responsibility |
|---|---|
| `app.py` | FastAPI app, lifespan, background tasks, `/health` and `/ready` |
| `config.py` | `pydantic-settings` configuration — every env var with its default |
| `db.py` | `SCHEMA_DDL`, applied idempotently at startup; this service's schema only |
| `pipeline.py` | The message-handling flow |
| `models.py` | Internal value objects (not the wire contract — that is in `shared`) |
| `exceptions.py` | Typed exceptions, distinguishing transient from terminal failures |
| `adapters/` | External-provider clients, where present |

## POC harnesses

Standalone, standard-library research code kept for the evidence it produced. It is **not** imported
by any service and does not run in the stack.

| Harness | Question it answered |
|---|---|
| [poc/poc6/](poc/poc6/) | Does KG-plus-LLM arbitration beat graph-only within a fixed token budget? (`STOP`) |
| [poc/poc7-biquote-market-data/](poc/poc7-biquote-market-data/) | Can biquote.io replace Yahoo as the price provider? |
| [poc/poc8-freenewsapi/](poc/poc8-freenewsapi/) | Can FreeNewsApi.io replace GDELT as a news source? |

Each has its own README with run commands and gate results.

## Working here

The [Market Data snapshot package](services/market-data/market_data/snapshots/) contains the separate
worker, browser adapter, scheduler, mapping CLI and read-only router; `Dockerfile.snapshots` and its
hash-pinned `requirements-snapshots.txt` isolate browser dependencies from the API image.
[sampled.py](services/verification/verification/sampled.py) and
[sampled_policy.py](services/verification/verification/sampled_policy.py) own the separate shadow path.
Behavior and configuration are in [SRS-05](../requirements/SRS-05-market-data.md) and
[SRS-06](../requirements/SRS-06-verification.md).

Run each suite separately (their test-package names overlap):

```bash
python -m pytest src/services/market-data/tests -m "not integration"
python -m pytest src/services/verification/tests -m "not integration"
python -m pytest src/shared/tests -m "not integration"
```

Optional snapshot SQL tests require `SNAPSHOT_TEST_DATABASE_URL` pointing to a disposable database
named `snapshot_test`; they reset snapshot tables. Run `test_snapshot_storage.py` in Market Data and
`test_sampled_storage.py` in Verification separately. The optional shared `test_snapshot_broker.py`
requires `SNAPSHOT_TEST_RABBITMQ_URL` pointing to a disposable broker. Never use application resources.
Compile the browser lock with `pip-compile --generate-hashes
--output-file=src/services/market-data/requirements-snapshots.txt
src/services/market-data/requirements-snapshots.in` after a deliberate Playwright version change.

The cache regression in Market Data's `test_snapshots.py` runs with `SNAPSHOT_TEST_BROWSER=1` and
installed Chromium (available in the snapshot image). It uses a local HTTP fixture, not Avanza.

One shared virtual environment at the repository root (`.venv/`) serves every service — see the
repository [README](../README.md#development-environment). Validation from `src/shared/`:

```bash
python -m pytest
python -m ruff check .
python -m mypy shared tests
```

All code must pass `ruff check` and `mypy --strict`. When you change observable behaviour, update the
service's SRS in the same change — a requirement with no proving test is `Approved`, not
`Implemented`.
