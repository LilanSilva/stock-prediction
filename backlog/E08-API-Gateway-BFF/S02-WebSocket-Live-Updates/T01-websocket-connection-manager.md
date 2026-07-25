# T01: WebSocket Connection Manager

## Context

The API Gateway needs to push live updates to all connected Dashboard clients without requiring the Dashboard to poll. This task implements the WebSocket endpoint at `/ws`, a connection manager class that tracks active WebSocket connections, and an `aio-pika` RabbitMQ consumer that subscribes to the `predictions` and `scored-predictions` queues and fans each incoming message out to all connected clients. This is the only component in the system that bridges the async message bus to the synchronous HTTP/WebSocket layer.

## Background

FastAPI supports WebSockets natively via `fastapi.WebSocket`. Multiple Dashboard tabs or monitoring tools may connect simultaneously; the connection manager must track all of them and broadcast to all without one slow client blocking others.

The gateway acts as a **read-only consumer** on RabbitMQ. It does not acknowledge messages in a way that would remove them from persistent queues used by other services. To avoid consuming messages that other services need, the gateway must use a **non-durable, exclusive, auto-delete queue** bound to the same exchange as the main queues, or use `aio-pika`'s `ExchangeType.FANOUT` / topic exchange pattern.

The correct pattern:
1. The existing services publish to a `topic` exchange named `feed_analyzer` with routing keys `prediction.made` and `prediction.scored`
2. The gateway declares its own temporary queue (auto-delete, non-durable) and binds it to the exchange with both routing keys
3. This ensures the gateway gets a copy of every message without interfering with the other consumers' queues

If the exchange/routing key pattern has not been established by other services yet, the gateway should also be prepared to consume directly from the named queues `predictions` and `scored-predictions` as a fallback. Use the topic exchange approach as the primary design.

**Message schemas (from `src/shared/schemas.py`):**
- `PredictionMade` fields: `prediction_id`, `event_ids`, `asset`, `direction`, `magnitude_bucket`, `confidence`, `time_horizon`, `rationale`, `contributing_edges`, `correlation_id`, `created_at`
- `PredictionScored` fields: `prediction_id`, `asset`, `predicted_direction`, `actual_direction`, `predicted_magnitude`, `actual_magnitude`, `is_correct`, `score`, `contributing_edges`, `sources`, `scored_at`

**Wire format pushed to WebSocket clients:**
```json
{"type": "prediction", "data": { ...PredictionMade fields... }}
{"type": "score", "data": { ...PredictionScored fields... }}
```

## Inputs

- RabbitMQ connection: env var `RABBITMQ_URL` (e.g., `amqp://guest:guest@rabbitmq:5672/`)
- Exchange name: `feed_analyzer` (topic exchange), routing keys `prediction.made` and `prediction.scored`
- Fallback queue names: `predictions` (for `PredictionMade`), `scored-predictions` (for `PredictionScored`)
- Incoming WebSocket upgrade requests from Dashboard clients at `ws://host/ws`

## Outputs

- WebSocket JSON messages pushed to all connected clients:
  - `{"type": "prediction", "data": {...}}` when a `PredictionMade` arrives
  - `{"type": "score", "data": {...}}` when a `PredictionScored` arrives
- No writes to Postgres or Neo4j
- No messages published to RabbitMQ

## Technical Requirements

1. **File locations:**
   - `services/api-gateway/websocket_manager.py` - `ConnectionManager` class
   - `services/api-gateway/rabbitmq_subscriber.py` - RabbitMQ consumer coroutine
   - `services/api-gateway/routers/ws.py` - FastAPI WebSocket route

2. **ConnectionManager class** in `websocket_manager.py`:
   ```python
   class ConnectionManager:
       def __init__(self) -> None:
           self.active_connections: list[WebSocket] = []

       async def connect(self, websocket: WebSocket) -> None: ...
       def disconnect(self, websocket: WebSocket) -> None: ...
       async def broadcast(self, message: str) -> None: ...
   ```
   - `connect`: call `await websocket.accept()` then append to `active_connections`
   - `disconnect`: remove from `active_connections` (use `list.remove()` - safe because WebSocket objects are unique)
   - `broadcast`: iterate over a **copy** of `active_connections` (`list(self.active_connections)`) and call `await ws.send_text(message)` on each; catch `WebSocketDisconnect` and `RuntimeError` per connection and call `self.disconnect(ws)` without re-raising

3. **WebSocket endpoint** in `routers/ws.py`:
   ```python
   @router.websocket("/ws")
   async def websocket_endpoint(websocket: WebSocket) -> None:
       await manager.connect(websocket)
       try:
           while True:
               await websocket.receive_text()  # keep-alive; ignore client messages
       except WebSocketDisconnect:
           manager.disconnect(websocket)
   ```
   The `manager` is a module-level singleton imported from `websocket_manager.py`.

4. **RabbitMQ subscriber** in `rabbitmq_subscriber.py`:
   - Use `aio_pika.connect_robust` with `RABBITMQ_URL`. Robust connection auto-reconnects on broker restart.
   - Declare a `TopicExchange` named `feed_analyzer` (passive=False, durable=True)
   - Declare an exclusive, auto-delete, non-durable queue (no name - let RabbitMQ assign one)
   - Bind the queue to the exchange with routing key `prediction.made`
   - Bind the queue with routing key `prediction.scored`
   - Consume with `queue.consume(on_message)`
   - In `on_message(message: aio_pika.IncomingMessage)`:
     - Parse `message.routing_key` to determine type
     - Deserialize body with `PredictionMade.model_validate_json(message.body)` or `PredictionScored.model_validate_json(message.body)`
     - Build the wire-format dict: `{"type": "prediction", "data": prediction.model_dump(mode="json")}`
     - Call `await manager.broadcast(json.dumps(payload))`
     - Acknowledge the message: `await message.ack()`
   - The subscriber coroutine must run as a background `asyncio.Task` started in the FastAPI `lifespan` context manager

5. **Startup/shutdown in lifespan** (`services/api-gateway/main.py`):
   ```python
   @asynccontextmanager
   async def lifespan(app: FastAPI):
       # startup
       subscriber_task = asyncio.create_task(start_rabbitmq_subscriber(manager))
       app.state.subscriber_task = subscriber_task
       yield
       # shutdown
       subscriber_task.cancel()
       try:
           await subscriber_task
       except asyncio.CancelledError:
           pass
   ```

6. **Error handling in subscriber:**
   - Catch `aio_pika.exceptions.AMQPConnectionError` and log with `structlog`; the `connect_robust` client handles reconnect automatically
   - Catch JSON parse errors (`pydantic.ValidationError`) per message; log the error with the raw body and `ack` the message anyway to avoid infinite requeue loops
   - If the subscriber task crashes unexpectedly, log the exception with full traceback and attempt to restart after a 5-second delay (implement with a retry loop in `start_rabbitmq_subscriber`)

7. **Thread safety:** FastAPI runs in a single-threaded asyncio event loop. `ConnectionManager.active_connections` is a plain list - no locking needed because all operations run in the same event loop. Document this assumption in a code comment.

8. **Dependencies:** Add to `services/api-gateway/requirements.txt`: `aio-pika>=9.0`, `pydantic>=2.0`

9. **Singleton manager:** The `ConnectionManager` instance must be created at module level in `websocket_manager.py` and imported by both `routers/ws.py` and `rabbitmq_subscriber.py`. Do NOT create separate instances.

10. **Heartbeat/ping:** The WebSocket endpoint calls `receive_text()` in a loop to detect client disconnects. FastAPI/Starlette handles the underlying TCP keep-alive. No explicit ping/pong needed for this implementation.

## Acceptance Criteria

1. `GET /ws` (WebSocket upgrade) is accepted and the connection stays open
2. Publishing a `PredictionMade` message to the `prediction.made` routing key on the `feed_analyzer` exchange causes all connected WebSocket clients to receive `{"type": "prediction", "data": {...}}` within 500 ms
3. Publishing a `PredictionScored` message to `prediction.scored` causes all connected WebSocket clients to receive `{"type": "score", "data": {...}}` within 500 ms
4. With two WebSocket clients connected, both receive the broadcast (fan-out confirmed)
5. When a WebSocket client disconnects, subsequent broadcasts do not raise errors and the disconnected client is removed from `active_connections`
6. When the RabbitMQ broker is temporarily unavailable, the subscriber reconnects automatically when RabbitMQ becomes available again (test by restarting the RabbitMQ container)
7. A `pydantic.ValidationError` on a malformed message logs the error and does not crash the subscriber task
8. Shutting down the FastAPI app (SIGTERM) cancels the subscriber task cleanly with no unhandled exceptions in the logs
9. Zero active WebSocket connections: broadcasting a message produces no errors
10. All tests in `services/api-gateway/tests/test_websocket.py` pass

## Implementation Notes

- **Why a copy in broadcast:** Iterating `list(self.active_connections)` instead of the list directly prevents `RuntimeError: list changed size during iteration` when a disconnect is detected mid-broadcast and the connection is removed.
- **aio-pika exclusive queue:** An exclusive queue is automatically deleted when the consumer disconnects. This means each gateway instance gets its own copy of every message, which is correct for fan-out. If running multiple gateway replicas, each replica gets its own queue and each broadcasts to its own set of connected clients - this is the intended behavior.
- **Message acknowledgment strategy:** Use `auto_ack=False` (default). Manually `ack` after successful broadcast. This means if the gateway crashes after receiving a message but before acking it, the message is redelivered on reconnect. Since the queue is exclusive and auto-delete, this case is moot - but it's still good practice to ack explicitly.
- **RabbitMQ exchange vs direct queue:** If other services do not yet publish to a topic exchange and only publish to named queues, you may need to fall back to `aio_pika.Queue` consumption directly. Add an env var `RABBITMQ_USE_EXCHANGE=true` (default `true`) to toggle between the two approaches during development.
- **Structlog context:** Add `websocket_client_count=len(manager.active_connections)` to broadcast log lines so operators can see the fan-out size.
- **Testing WebSockets with pytest:** Use `httpx` with `httpx_ws` or Starlette's `TestClient` with `websocket_connect()`. Example:
  ```python
  from starlette.testclient import TestClient
  with TestClient(app).websocket_connect("/ws") as ws:
      # trigger broadcast
      data = ws.receive_json()
      assert data["type"] == "prediction"
  ```
- **RabbitMQ not available at startup:** `connect_robust` will keep retrying in the background. The FastAPI app should start and accept HTTP/WebSocket connections even if RabbitMQ is not yet available. The subscriber will connect when RabbitMQ becomes ready.

## Definition of Done

- [ ] Unit tests pass (`pytest services/api-gateway/tests/test_websocket.py`)
- [ ] Code passes `ruff check services/api-gateway/` with zero errors
- [ ] Code passes `mypy services/api-gateway/ --strict` with zero errors
- [ ] All 10 acceptance criteria verified
- [ ] `ConnectionManager` is a module-level singleton (not recreated per request)
- [ ] `broadcast` iterates over a copy of `active_connections`
- [ ] RabbitMQ subscriber started in FastAPI `lifespan`, cancelled cleanly on shutdown
- [ ] `aio_pika.connect_robust` used (not `connect`) for automatic reconnection
- [ ] Malformed messages are logged and acked without crashing the subscriber
- [ ] `aio-pika>=9.0` added to `services/api-gateway/requirements.txt`
