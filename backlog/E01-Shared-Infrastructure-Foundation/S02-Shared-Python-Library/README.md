# S02: Shared Python Library

> Contract-freeze status: governed by the [backlog override matrix](../../contract-freeze-overrides.md).
> Schemas implement the canonical envelope and contracts in `docs/contracts/message-contracts.md`
> (full envelope, dual-session prices, `decision_method`, canonical asset IDs). The messaging client
> publishes by routing key to the `feed.events` topic exchange.

## Overview

This story builds the `src/shared/` Python package that every microservice imports. It is the internal SDK for the feed-analyzer system: message contracts, the RabbitMQ client, the provider-configurable LLM gateway, and structured logging with end-to-end correlation ID tracking.

All six microservices and the API Gateway import from `src/shared/`. Getting this library right with clean Pydantic v2 schemas, a reliable async RabbitMQ wrapper, and a robust LLM gateway directly determines the quality and reliability of everything built in later epics.

For M1, only the Cleansing Service is allowed to call the shared LLM gateway. Prediction-time LLM arbitration is deferred after the POC-6 `STOP` result.

## Tasks

| Task | Description |
|---|---|
| T01 - Pydantic message schemas | All six queue message contracts as Pydantic v2 models with validation and serialization |
| T02 - RabbitMQ async client wrapper | aio-pika-based async publisher/consumer with retry, DLX routing, and connection pooling |
| T03 - LLM gateway wrapper | Provider-configurable LLM wrapper selected by environment settings, with structured output, retry, token/cost logging, and API-key based authentication |
| T04 - Structured logging & correlation ID middleware | structlog JSON logging and FastAPI middleware for correlation ID propagation |

## Dependencies

- S01 (Docker Compose & Infrastructure Setup) must be complete so that RabbitMQ and Postgres are available for integration tests.
- A single API key (`LLM_API_KEY`) for the configured provider must be set for T03 integration tests.
- Python 3.12 must be installed on the developer machine.

## LLM Configuration

The shared LLM gateway must not hardcode one provider or one model. Runtime configuration selects:

| Variable | Purpose |
|---|---|
| `LLM_PROVIDER` | Wire protocol/adapter; `openai` selects the OpenAI-compatible adapter (covers Kimi/Moonshot, Together, Azure OpenAI, local vLLM) |
| `LLM_MODEL` | Provider-specific model ID |
| `LLM_BASE_URL` | OpenAI-compatible endpoint URL; empty uses the provider default (`api.openai.com`) |
| `LLM_API_KEY` | The single API key for the configured provider |
| `LLM_MAX_INPUT_TOKENS` | Maximum compact input budget |
| `LLM_MAX_OUTPUT_TOKENS` | Low output cap for structured responses |
| `LLM_TIMEOUT_SECONDS` | Per-call timeout |

Provider/model changes must not require changes in service business logic, message contracts, or prompts beyond provider capability mapping.

## Package Structure

```text
src/shared/
  pyproject.toml
  src/shared/
    __init__.py
    schemas/
      __init__.py
      messages.py
    messaging/
      __init__.py
      client.py
      exceptions.py
    llm/
      __init__.py
      gateway.py
      providers.py
      settings.py
      prompts.py
    logging/
      __init__.py
      setup.py
      middleware.py
  tests/
    __init__.py
    test_schemas.py
    test_messaging.py
    test_llm_gateway.py
    test_logging.py
```

## How to Test End-to-End

1. Set up the shared root virtual environment (creates `.venv`, installs pinned hash-verified deps
   and the editable `shared` package):
   - Windows: `powershell -ExecutionPolicy Bypass -File scripts\setup-venv.ps1`
   - macOS/Linux: `bash scripts/setup-venv.sh`

   All services reuse this single root `.venv`; do not create a per-service environment. See the
   repository `README.md` (*Development environment*) for details.
2. Ensure S01 infrastructure is running: `docker compose --env-file infra/.env -f infra/docker-compose.yml up -d --build`
3. Set provider variables in `infra/.env`, for example `LLM_PROVIDER=openai`, `LLM_MODEL=<model-id>`, `LLM_BASE_URL=<endpoint-or-empty>`, and `LLM_API_KEY=<key>`.
4. Run all tests: `cd src/shared && python -m pytest`
5. Run linting and type checks: `python -m ruff check . && python -m mypy shared tests`
