# S02 - WebSocket Live Updates

## Overview

This story adds real-time push capability to the API Gateway. The Dashboard must receive live updates when new predictions are created and when existing predictions are scored, without polling. The implementation uses FastAPI's native WebSocket support combined with an RabbitMQ consumer that subscribes to the `predictions` and `scored-predictions` queues and fans the messages out to all currently connected Dashboard WebSocket clients.

This is a single-task story because the connection manager, RabbitMQ subscriber, and WebSocket endpoint are tightly coupled components that should be built and tested together.

## Tasks

| Task | Description |
|------|-------------|
| T01 | WebSocket connection manager, RabbitMQ subscriber, and `/ws` endpoint |

## Dependencies

Before this story can be started:

- S01 (Core REST API Endpoints) must be complete - the app startup lifecycle (`lifespan`) must already be wired
- RabbitMQ must be running and accessible. The `aio-pika` library must be in `services/api-gateway/requirements.txt`
- The `src/shared/` package must export `PredictionMade` and `PredictionScored` Pydantic schemas
- The `src/shared/` package must provide a `RabbitMQClient` wrapper or the gateway must implement its own `aio-pika` connection
- RabbitMQ connection string must be injectable via env var `RABBITMQ_URL` (e.g., `amqp://guest:guest@rabbitmq:5672/`)

## How to Test End-to-End

1. `docker compose up --build` from `infra/`
2. Open a WebSocket client (e.g., `websocat ws://localhost:8080/ws` or browser DevTools)
3. Confirm the connection is accepted and stays open
4. Publish a test `PredictionMade` message to the `predictions` RabbitMQ queue using the RabbitMQ management UI or a test script
5. Confirm the WebSocket client receives a JSON message with `type: "prediction"` within 500 ms
6. Publish a test `PredictionScored` message to `scored-predictions` queue
7. Confirm the WebSocket client receives a JSON message with `type: "score"` within 500 ms
8. Close the WebSocket client connection and confirm no errors appear in the gateway logs
9. Open two WebSocket clients simultaneously and confirm both receive the broadcast
10. Run `pytest services/api-gateway/tests/test_websocket.py -v`
