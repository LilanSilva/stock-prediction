# S01 - Core REST API Endpoints

## Overview

This story implements all synchronous REST endpoints that the React Dashboard consumes. The gateway reads exclusively from Postgres; it never writes to any database or publishes to any queue. All endpoints follow the same conventions: JSON responses, ISO-8601 timestamps, snake_case field names, and consistent error envelopes.

There are three task-groups of endpoints:

| Task | Endpoints | Purpose |
|------|-----------|----------|
| T01 | `/predictions`, `/predictions/{id}`, `/predictions/stats` | Prediction list, detail, and accuracy statistics |
| T02 | `/credibility/edges`, `/credibility/sources`, `/credibility/history/{edge_id}`, `/graph/assets/{symbol}` | Knowledge-graph credibility weights and history |
| T03 | `/events`, `/events/{id}`, `/prices/{symbol}`, `/health` | Detected events, price history, and service health |

## Dependencies

Before this story can be started the following must exist:

- Postgres schema migrations must have run and the tables `predictions`, `prediction_outcomes`, `events`, `articles`, `prices`, `credibility_edges`, `credibility_sources`, `credibility_edge_history` must exist
- The `src/shared/` package must expose Pydantic schemas: `PredictionMade`, `PredictionScored`, `EventDetected`, `PriceObserved`
- `services/api-gateway/` directory must exist with a `Dockerfile` and `requirements.txt`
- Environment variables `DATABASE_URL`, `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`, and per-service health URLs must be injectable via `.env` / Docker Compose
- The six backend services must expose `GET /health` endpoints

## How to Test End-to-End

1. `docker compose up --build` from `infra/`
2. Seed Postgres with fixture data using `pytest` fixtures or a seed SQL script
3. Run `pytest services/api-gateway/tests/ -v` - all unit and integration tests must pass
4. Open a browser or use `curl`/`httpie` to hit `http://localhost:8080/predictions` and confirm a paginated JSON response
5. Check filters: `GET /predictions?asset=GOLD&status=SCORED&limit=5` must return at most 5 scored gold predictions
6. Check `/health` - stop one backend service container and confirm the health endpoint reports it as `degraded`
