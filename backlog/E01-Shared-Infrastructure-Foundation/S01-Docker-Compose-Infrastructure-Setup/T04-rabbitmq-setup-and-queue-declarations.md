# T04: RabbitMQ Setup & Queue Declarations

## Context

This task creates the RabbitMQ configuration files that declare all six application queues with dead-letter exchanges (DLX), durability settings, and message TTL. RabbitMQ loads these definitions at container startup via the management plugin's `load_definitions` feature. This ensures every service starts up and finds its queue already declared — no service needs to declare queues itself.

This task belongs to the infrastructure story (S01). The files are volume-mounted into the `feed-rabbitmq` container defined in T01.

## Background

### Queue topology

The six queues form a pipeline:
```
Ingestion → [raw-news] → Cleansing → [events] → Prediction → [predictions]
                                                                     ↓
                    Verification ← [prices] ← Market Data ← [price-requests]
                          ↓
               [scored-predictions] → Credibility
```

Each queue is consumed by exactly one service. Each producing service publishes to exactly one queue.

### Dead-letter exchange pattern

When a message is rejected (basic.nack with requeue=false) or expires due to TTL, it is routed to the dead-letter exchange (`feed.dlx`). The DLX routes the message to a per-queue dead-letter queue (e.g., `raw-news.dlq`) where it can be inspected, replayed, or discarded without blocking the main queue.

### Durability

All queues and the DLX must be durable. RabbitMQ persists durable queues to disk. Combined with persistent message delivery mode (delivery_mode=2, set by the application), messages survive a RabbitMQ restart.

## Inputs

- T01's Docker Compose definition: `./rabbitmq/definitions.json` is volume-mounted to `/etc/rabbitmq/definitions.json`.
- RabbitMQ management plugin's definitions format (JSON schema documented at https://www.rabbitmq.com/docs/definitions).

## Outputs

Files created in `infra/rabbitmq/`:

```
infra/rabbitmq/
  definitions.json          # topology only; "users": [] (no secrets in git)
  rabbitmq.conf
  render-definitions.sh     # injects the broker user from env at container startup
```

## Technical Requirements

### `infra/rabbitmq/rabbitmq.conf`

Minimal config to enable definitions loading:

```ini
# RabbitMQ configuration
# Load the runtime-rendered definitions (topology + env-injected user), not the tracked template.
management.load_definitions = /var/lib/rabbitmq/rendered-definitions.json

# Logging
log.console = true
log.console.level = info

# Default vhost is / — no change needed
```

### `infra/rabbitmq/definitions.json`

The definitions file must declare:

**Virtual host**: use the default `/` vhost.

**Exchanges**:

| name | type | durable | purpose |
|---|---|---|---|
| `feed.dlx` | `direct` | true | Dead-letter exchange for all queues |
| `feed.default` | `direct` | true | Default routing exchange (optional, explicit declaration) |

**Queues** (all durable, all with DLX configured):

| Queue name | Dead-letter queue | Message TTL | Notes |
|---|---|---|---|
| `raw-news` | `raw-news.dlq` | 24h (86400000ms) | Published by Ingestion |
| `events` | `events.dlq` | 24h | Published by Cleansing |
| `predictions` | `predictions.dlq` | 48h (172800000ms) | Published by Prediction |
| `price-requests` | `price-requests.dlq` | 1h (3600000ms) | Published by Verification |
| `prices` | `prices.dlq` | 24h | Published by Market Data |
| `scored-predictions` | `scored-predictions.dlq` | 7d (604800000ms) | Published by Verification |

**Dead-letter queues** (also durable, no DLX, no TTL — hold messages indefinitely for manual inspection):

`raw-news.dlq`, `events.dlq`, `predictions.dlq`, `price-requests.dlq`, `prices.dlq`, `scored-predictions.dlq`

**Bindings**:

For each main queue, bind it to `feed.default` exchange with its queue name as the routing key. For each DLQ, bind it to `feed.dlx` with the main queue's name as the routing key.

**Queue arguments for main queues**:

```json
"arguments": {
  "x-dead-letter-exchange": "feed.dlx",
  "x-dead-letter-routing-key": "<queue-name>",
  "x-message-ttl": <ttl-in-ms>,
  "x-queue-type": "classic"
}
```

**Users** (injected at container startup from environment variables — never stored in git):

```json
"users": [
  {
    "name": "feed_user",
    "password_hash": "",
    "hashing_algorithm": "rabbit_password_hashing_sha256",
    "tags": "administrator"
  }
]
```

Note: when `management.load_definitions` is configured, RabbitMQ logs `Will not seed default virtual
host and user: have definitions to load...` and **does not** create the `RABBITMQ_DEFAULT_USER`. The
definitions file is the sole source of users, so an empty `users` array leaves the broker with no
AMQP login. To keep credentials in environment variables only (no `password_hash` committed to git),
the tracked `definitions.json` keeps `"users": []` and an entrypoint script renders a runtime copy:

- `infra/rabbitmq/render-definitions.sh` reads `RABBITMQ_DEFAULT_USER` / `RABBITMQ_DEFAULT_PASS`,
  computes the salted hash with `rabbitmqctl hash_password` (offline, no node required), and writes
  the user + `/` permissions into `/var/lib/rabbitmq/rendered-definitions.json`.
- `rabbitmq.conf` sets `management.load_definitions` to that rendered file.
- The compose entrypoint runs the render script (stripping any CRLF) before `rabbitmq-server`.

This keeps the tracked `definitions.json` valid JSON with no secrets, while the broker user comes
entirely from `infra/.env`. Other services and coding agents must follow the same pattern: put every
secret in `infra/.env` and inject at runtime; never commit a password or password hash.

**Complete `definitions.json` structure**:

```json
{
  "rabbitmq_version": "3.13.0",
  "vhosts": [{"name": "/"}],
  "exchanges": [...],
  "queues": [...],
  "bindings": [...]
}
```

## Acceptance Criteria

1. After `docker compose up`, the RabbitMQ management UI at `http://localhost:15672` (guest/guest or feed_user credentials) shows all six main queues listed under the `/` vhost.
2. All six main queues have `Durable: true` in the queue details view.
3. All six dead-letter queues (`raw-news.dlq`, etc.) are also present and durable.
4. Each main queue's arguments show `x-dead-letter-exchange: feed.dlx` and the correct TTL value.
5. The `feed.dlx` direct exchange exists and is durable.
6. Publishing a message to `raw-news` via the management UI "Publish message" feature and then nacking it (simulated via the management UI "Get message" → nack) routes it to `raw-news.dlq`.
7. `docker compose down -v && docker compose up` re-creates all queues without manual steps.
8. RabbitMQ logs (checked via `docker logs feed-rabbitmq`) show successful definitions loading with no errors.

## Implementation Notes

- The `rabbitmq:3.13-management` image includes both the broker and the management plugin — no additional plugin activation needed.
- The `load_definitions` feature is the recommended way to pre-declare infrastructure in RabbitMQ 3.13+. It is preferred over the older `RABBITMQ_SERVER_ADDITIONAL_ERL_ARGS` approach because it is parsed at broker start, not as Erlang flags.
- The `x-queue-type: classic` argument is explicit but not strictly required (classic is the default). Include it to make the queue type visible in the definitions file for documentation purposes.
- Message TTL (`x-message-ttl`) is set per queue based on the expected processing time for each stage:
  - `price-requests` is 1 hour because a price request older than the market window is useless.
  - `scored-predictions` is 7 days because the credibility service may have a processing backlog.
  - All others are 24 hours as a safe default.
- Do not use quorum queues (`x-queue-type: quorum`) — they require a RabbitMQ cluster of at least 3 nodes, which is not applicable to the single-node local development setup.
- The `aio-pika` client wrapper (built in S02/T02) will use the same queue names defined here. The names must match exactly: `raw-news`, `events`, `predictions`, `price-requests`, `prices`, `scored-predictions`.
- RabbitMQ's definitions JSON is sensitive to the `rabbitmq_version` field — use the actual version of the image (3.13.0).

## Definition of Done

- [x] `infra/rabbitmq/definitions.json` is valid JSON (validate with `python -m json.tool definitions.json`)
- [x] `infra/rabbitmq/rabbitmq.conf` exists with `management.load_definitions` pointing to the rendered definitions path
- [x] `infra/rabbitmq/render-definitions.sh` injects the broker user from `RABBITMQ_DEFAULT_USER` / `RABBITMQ_DEFAULT_PASS` at startup
- [x] All six main queues appear in RabbitMQ management UI after `docker compose up`
- [x] All six dead-letter queues appear in management UI
- [x] `feed.dlx` exchange exists and is durable
- [x] Each main queue has the correct `x-dead-letter-exchange` argument (`x-message-ttl` is not required by the frozen `docs/contracts/message-contracts.md` and is intentionally omitted)
- [ ] `docker compose down -v && docker compose up` restores all queues without errors
- [x] AMQP login with the `.env` credentials succeeds (broker user is created from env, not committed)
- [x] No secrets are hardcoded in `definitions.json` or any tracked file (credentials managed via env vars)
