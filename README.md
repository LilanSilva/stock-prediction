# Feed Analyzer

Feed Analyzer is a POC for predicting short-horizon market direction from global and Swedish news. Its distinguishing requirement is to preserve distinct concurrent events, combine all relevant forces affecting the same asset, resolve conflicts, verify the prediction against market closes, and learn from the outcome.

## Current status

The architecture and backlog are aligned around a token-efficient POC. P06 remediation completed the controlled POC-6 rerun on 2026-07-13 and recorded `STOP`: KG-plus-LLM arbitration did not improve enough over graph-only to justify implementation in the next walking skeleton. The next build scope should use graph-only prediction while LLM arbitration remains deferred unless a new hypothesis is approved.

## Authoritative documentation

Read these in order:

1. [Agreed system requirements](docs/requirements/agreed-system-requirements.md)
2. [Canonical message contracts](docs/contracts/message-contracts.md)
3. [Asset registry](docs/reference/asset-registry.md)
4. [Event taxonomy](docs/reference/event-taxonomy.md)
5. [Architecture decisions](docs/decisions/README.md)
6. [Core acceptance map](docs/requirements/core-acceptance-map.md)
7. [Functional and architecture index](docs/README.md)
8. [Backlog and milestones](backlog/README.md)
9. [POC-6 result and controlled-rerun plan](backlog/POC/poc-6-end-to-end-prediction-validation.md)
10. [Executable POC-6 harness](src/poc/poc6/README.md)

Executable Pydantic models in the future `shared` package are the source of truth for message fields. Generated contract documentation must remain consistent with those models.

## Repository layout

- `src/shared/` — installable shared Python package and its tests.
- `src/poc/poc6/` — completed POC-6 evaluation harness, fixtures, and recorded results.
- `infra/` — Docker Compose and database/broker initialization.
- `docs/` — authoritative requirements, contracts, decisions, and architecture.
- `backlog/` — epics and tasks interpreted through the contract-freeze overrides.

## Development environment

This repository uses **one shared virtual environment at the repository root** (`.venv/`). Every
service (E02–E09) imports the same editable `shared` package from this single environment. Do not
create per-service virtual environments and do not install project packages into a machine-level
(global) interpreter.

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

### Validate the shared package

```bash
cd src/shared
python -m pytest
python -m ruff check .
python -m mypy shared tests
```

### Unit-test/integration-test debugging policy (for coding agents)

When a unit test fails, a coding agent should first attempt a fix. However, if the investigation
exceeds roughly **3 minutes of effort or a comparable token budget without a clear resolution**,
stop investigating and hand the failure back to a human with:

- the failing test name and file,
- the observed vs. expected behavior,
- the most likely root cause, and
- a concrete suggested fix (without applying it).

Do not spend extended time or tokens brute-forcing test failures. Prefer a fast, documented handoff
so a human can debug efficiently. This policy applies to all agents working in this repository.

## Local infrastructure

The local stack (Postgres + pgvector, Neo4j, RabbitMQ) is defined in
[infra/docker-compose.yml](infra/docker-compose.yml). Bring it up with:

```bash
cp infra/.env.example infra/.env   # then edit infra/.env with local values
docker compose --env-file infra/.env -f infra/docker-compose.yml up -d --build
```

Two environment-specific notes apply to this repository:

- **Postgres/pgvector image:** the community `pgvector/pgvector` image may be blocked by org
  registry policy, so Postgres is built from the org-approved official `postgres:16` base plus the
  `pgvector` package — see [infra/postgres-image/Dockerfile](infra/postgres-image/Dockerfile). The
  compose `feed-postgres` service uses `build:` instead of a community `image:`.
- **RabbitMQ user:** when a definitions file is loaded, RabbitMQ will not seed the
  `RABBITMQ_DEFAULT_USER`, so the broker user is injected at startup from environment variables by
  [infra/rabbitmq/render-definitions.sh](infra/rabbitmq/render-definitions.sh) (the password hash is
  computed at runtime, never committed).

### Secrets and environment variables (all agents follow this)

Every secret lives in **one** place: `infra/.env` (git-ignored). Nothing secret is ever written to a
tracked file.

- Document each variable — names and placeholder values only — in
  [infra/.env.example](infra/.env.example); never put real values there.
- Services and tests read credentials from environment variables
  (`DATABASE_URL`, `NEO4J_URI`, `RABBITMQ_URL`, `RABBITMQ_DEFAULT_USER/PASS`, LLM keys, …).
- Never hardcode a password, token, or even a password **hash** in a tracked file. If a tool needs a
  derived credential (like RabbitMQ's `password_hash`), compute it at runtime from the env var, as
  the RabbitMQ render script does.
- `infra/.env` and `.env` are already excluded in [.gitignore](.gitignore); keep them untracked.

## POC scope

- Initial validated POC assets: Gold and Brent oil using the P06 Yahoo reference-close policy.
- Initial horizon: one trading day, close-to-close.
- Initial news sources: a deliberately small subset selected from the proven sources.
- LLM calls: allowed only for explicitly approved experiments or ambiguous cleansing cases; prediction-time LLM arbitration is blocked by the POC-6 `STOP` result.
- No automated trade execution.
- Local-only API and Dashboard until production security work is explicitly approved.

## Delivery sequence

1. Freeze contracts, registries, database topology, and queue bindings.
2. Build a thin graph-only end-to-end walking skeleton for Gold and Brent oil.
3. Keep prediction-time LLM arbitration out of M1 unless a new controlled hypothesis is approved.
4. Add reliability and security required for persistent use.
5. Expand source, asset, API, and Dashboard scope only after validation.
