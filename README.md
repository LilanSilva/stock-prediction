# Feed Analyzer

Feed Analyzer is a local proof of concept that tests one hypothesis:

> Can structured news events combined with a causal knowledge graph predict the next trading
> session's price direction for a given asset?

It reads news, groups articles describing the same real-world event, maps that event to causal factors
in a knowledge graph, produces a directional prediction with confidence and magnitude, waits for the
market to close, scores the prediction against the actual price move, and feeds the outcome back to
adjust the graph's edge weights.

The loop is: **news → event → prediction → price → score → learning.**

It does not execute trades or provide personalised financial advice.

## Repository structure

Each folder below has its own `README.md` explaining its inner folders and files. **Start with the
README of the folder you need** — this file only says which folder that is.

| Folder | Purpose | Its README |
|---|---|---|
| [requirements/](requirements/) | **All requirement documents and the system's functional knowledge.** The authoritative specification: what the system must do, how each service works internally, and which test proves each requirement. | [requirements/README.md](requirements/README.md) |
| [src/](src/) | **All system code.** The shared library, the seven services, and the completed POC harnesses. | [src/README.md](src/README.md) |
| [backlog/](backlog/) | **Backlog tasks (epics, stories, tasks) and POC documentation.** Unbuilt work only, plus the proof-of-concept research that justified the technical choices. | [backlog/README.md](backlog/README.md) |
| [docs/](docs/) | **System architecture diagrams** and other system documents not covered by a requirement specification. | [docs/README.md](docs/README.md) |
| [scripts/](scripts/) | **Manual scripts users run** — environment setup, registry validation, seed generation. | [scripts/README.md](scripts/README.md) |
| [infra/](infra/) | **All infrastructure code** — the Docker Compose stack, database and broker initialisation, message topology, graph seed data. | [infra/README.md](infra/README.md) |

> **Note:** [requirements.txt](requirements.txt) is the pinned dependency lockfile and has nothing to do
> with the [requirements/](requirements/) folder, which holds specifications.

## Where to start

- **Changing a service?** Read its SRS in [requirements/](requirements/README.md) first — it lists every
  requirement, message, table, config key, and the tests that protect existing behaviour.
- **Understanding the whole system?** [requirements/SyRS-system.md](requirements/SyRS-system.md).
- **Wondering why a technical choice was made?** [backlog/POC/](backlog/POC/README.md) for the research,
  [requirements/ADR-decisions.md](requirements/ADR-decisions.md) for the architecture decisions.
- **Setting up locally?** *Development environment* below.

## Source of truth

Executable code outranks every document: the Pydantic models in
[src/shared/shared/schemas/](src/shared/shared/schemas/) and the
[asset registry JSON](src/shared/shared/reference/assets.json) are the running contract, and the
specifications in [requirements/](requirements/README.md) describe it.

The full authority order, and what to do when two sources disagree, is in
[requirements/README.md](requirements/README.md#authority).

## Current status

The seven services (shared foundation, ingestion, cleansing, prediction, market data, verification,
credibility) are built and specified. Notification is specified and `Approved`. The API Gateway and
Dashboard are not built — see [backlog/](backlog/README.md).

Prediction is graph-only and makes zero LLM calls: the POC-6 controlled rerun recorded `STOP` for
prediction-time LLM arbitration. Details in
[backlog/POC/poc-6-end-to-end-prediction-validation.md](backlog/POC/poc-6-end-to-end-prediction-validation.md).

## Development environment

This repository uses **one shared virtual environment at the repository root** (`.venv/`). Every
service imports the same editable `shared` package from this single environment. Do not create
per-service virtual environments and do not install project packages into a machine-level (global)
interpreter.

Dependencies are pinned and checksum-verified in [requirements.txt](requirements.txt), which is
compiled from [src/shared/pyproject.toml](src/shared/pyproject.toml) (including the `dev` and `llm`
extras) with `pip-compile --generate-hashes`. Installing with `--require-hashes` aborts if any
downloaded package does not match its recorded SHA256 hash.

### First-time setup

The fastest path is the setup script, run once from the repository root (Python 3.12+ required). It
creates `.venv`, upgrades pip, installs the exact hash-verified dependencies, and installs the local
`shared` package editable. It is safe to re-run to sync after the lockfile changes.

Windows (PowerShell):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup-venv.ps1
```

macOS/Linux (bash):

```bash
bash scripts/setup-venv.sh
```

Then select `.venv` as the workspace interpreter in VS Code
(Command Palette → **Python: Select Interpreter** → `.venv`). All services reuse this interpreter.

<details>
<summary>Manual equivalent (if you prefer not to run the script)</summary>

Windows (PowerShell):

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -e src\shared --no-deps
```

macOS/Linux (bash):

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install --require-hashes -r requirements.txt
./.venv/bin/python -m pip install -e src/shared --no-deps
```

</details>

### Regenerating the lockfile

After changing dependencies in [src/shared/pyproject.toml](src/shared/pyproject.toml), recompile the
pinned, hashed lockfile:

```bash
python -m pip install pip-tools
python -m piptools compile --generate-hashes --extra dev --extra llm \
  --output-file requirements.txt src/shared/pyproject.toml
```

Validation commands for the shared package are in [src/README.md](src/README.md#working-here).
Conventions for coding agents — including the test-failure handoff policy — are in
[.github/copilot-instructions.md](.github/copilot-instructions.md).

## Local infrastructure

Bring up the local stack (Postgres + pgvector, Neo4j, RabbitMQ) from the repository root:

```bash
cp infra/.env.example infra/.env   # then edit infra/.env with local values
docker compose --env-file infra/.env -f infra/docker-compose.yml up -d --build
```

Once that stack is up, redeploying after a code change is
[scripts/build-and-deploy.ps1](scripts/build-and-deploy.ps1) — it rebuilds the service images and
recreates only the service containers, leaving Postgres, Neo4j, RabbitMQ and the graph seed alone:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build-and-deploy.ps1
```

**Every secret lives in `infra/.env` only** (git-ignored) — never in a tracked file, not even a
password hash. That rule, the environment-specific image and broker notes, and how to validate an
infrastructure change are all in [infra/README.md](infra/README.md).

## Scope boundaries

- Assets: declared in the [asset registry](requirements/REF-02-asset-registry.md) — adding a market is a
  registry edit, not a code change.
- Horizon: one trading day, close-to-close.
- LLM calls: only for ambiguous cleansing extraction or factual-conflict resolution. Never at
  prediction time.
- No automated trade execution.
- Local-only deployment until production security work is explicitly approved.

Full scope, including what is explicitly out of scope and why, is in
[requirements/SyRS-system.md](requirements/SyRS-system.md#2-purpose-and-scope).
