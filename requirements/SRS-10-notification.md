# SRS-10: Notification Service

## 1. Document control

| | |
|---|---|
| Document ID | `SRS-10` |
| Component | Notification Service |
| Requirement ID prefix | `NTF` |
| Status | `Approved` |
| Version | `1.0.0` |
| Source code | [`src/services/notification/notification/`](../src/services/notification/notification/) |
| Tests | None — `src/services/notification/tests/` does not exist yet, which is why this document is `Approved` rather than `Implemented` |
| Owned schema | None — no database tables; recipient lists are file-based |
| Last verified against code | `—` |

## 2. Purpose and scope

### 2.1 What this component does

The Notification Service is the outbound alert layer of the Feed Analyzer pipeline. It consumes
`PredictionMade` and `PredictionScored` messages, builds human-readable notifications, and delivers
them through all configured channels concurrently. Each channel formats the message in the way
natural to its medium and delivers it to its own list of recipients.

It is deliberately unintelligent about predictions. It does not re-evaluate whether a prediction is
good, filter by asset, or correlate with market data — it only guarantees that **a qualifying
prediction or verification result is announced exactly once to every configured channel, with
failure in one channel never blocking the others.**

### 2.2 In scope

- Consuming `PredictionMade` messages from the `notification.predictions` queue and dispatching prediction alerts.
- Consuming `PredictionScored` messages from the `notification.scored` queue and dispatching verification result alerts.
- Applying a configurable confidence threshold before dispatching prediction alerts.
- Resolving asset display details (company name, exchange, ticker) from the shared asset registry.
- Building a `NotificationMessage` value object from the prediction and asset details.
- Building a `VerificationMessage` value object from the scored prediction and asset details.
- Dispatching messages to all registered channels concurrently.
- Email notifications via the Brevo transactional email API.
- WhatsApp notifications via the Meta Cloud API.
- Per-channel recipient lists loaded from JSON files at startup.
- An extension point (`NotificationChannel` Protocol) so future channels require no engine changes.

### 2.3 Explicitly out of scope

| Out of scope | Where it belongs |
|---|---|
| Deciding whether a prediction is correct | Verification Service |
| Scoring or weighting predictions | Credibility Service |
| Persisting notification history | Not required in this iteration |
| Personalised per-recipient filtering | Deferred; all registered recipients receive every alert |
| Recipient management via a database | Deferred; files are used for this iteration |
| Unsubscribe handling | Deferred |
| Rich media (charts, images) in messages | Deferred |
| Publishing any message to the bus | This service has no outbound bus messages |
| Filtering verification alerts by correctness | All `PredictionScored` messages trigger an alert regardless of `is_correct` |

## 3. Definitions

| Term | Meaning |
|---|---|
| Channel | One outbound delivery mechanism (email, WhatsApp, etc.) identified by a `channel_id` string |
| `NotificationChannel` | The Python Protocol every channel implementation satisfies |
| `NotificationMessage` | The value object built from a `PredictionMade` and the asset registry; passed to every channel for prediction alerts |
| `VerificationMessage` | The value object built from a `PredictionScored` and the asset registry; passed to every channel for verification result alerts |
| Recipient | A destination address for one channel — an email address or an E.164 phone number |
| Recipient file | A JSON file containing the list of recipients for one channel |
| Confidence gate | The minimum confidence value a prediction must carry to trigger a prediction alert (not applied to verification alerts) |
| Dispatch | The act of calling every registered channel's `send()` or `send_scored()` method concurrently |
| Channel registry | The in-process collection of `NotificationChannel` instances active at runtime |
| `signal_strength` | A human-readable rendering of `PredictionMade.magnitude`: `LARGE` → `HIGH`, `MEDIUM` → `MEDIUM`, `SMALL` → `LOW` |
| Brevo | The transactional email API used for the email channel |
| Meta Cloud API | The WhatsApp Business API provided by Meta, used for the WhatsApp channel |

## 4. System context

### 4.1 Position in the pipeline

```text
  Prediction Service                        Verification Service
        | prediction.made                         | prediction.scored
        v                                         v
  feed.events exchange (topic, durable)
        |                                         |
        +---> notification.predictions            +---> notification.scored
                      |                                       |
                      +-------------------+-------------------+
                                          |
                              NOTIFICATION SERVICE
                                          |
                          +--------------+--------------+
                          |                             |
                  EmailChannel                 WhatsAppChannel
                  (Brevo API)                  (Meta Cloud API)
                          |                             |
               email recipients list         phone numbers list
               (email_recipients.json)       (whatsapp_recipients.json)
```

### 4.2 Dependencies

| Dependency | Purpose | Failure impact |
|---|---|---|
| RabbitMQ `notification.predictions` queue | Source of `PredictionMade` messages | No prediction alerts dispatched; messages queue and deliver when the service recovers |
| RabbitMQ `notification.scored` queue | Source of `PredictionScored` messages | No verification alerts dispatched; messages queue and deliver when the service recovers |
| Shared asset registry (`assets.json`) | Resolve company name, exchange, and ticker from `asset_id` | Service fails to start if the registry cannot be loaded |
| Brevo transactional email API | Deliver email notifications | Email channel fails; WhatsApp channel is unaffected |
| Meta Cloud API | Deliver WhatsApp notifications | WhatsApp channel fails; email channel is unaffected |
| `email_recipients.json` | Email recipient list | Email channel disabled if file is absent or empty |
| `whatsapp_recipients.json` | WhatsApp recipient list | WhatsApp channel disabled if file is absent or empty |

## 5. Functional requirements

### 5.1 Message consumption

| ID | Requirement | Priority | Status |
|---|---|---|---|
| `NTF-1` | The service **shall** consume `PredictionMade` messages from the `notification.predictions` queue. | Must | Approved |
| `NTF-2` | The service **shall** acknowledge a message only after all channel dispatches have completed or failed. | Must | Approved |
| `NTF-3` | The service **shall** dead-letter a message after the configured maximum retry count is exceeded. | Must | Approved |
| `NTF-39` | The service **shall** consume `PredictionScored` messages from the `notification.scored` queue. | Must | Approved |
| `NTF-40` | The service **shall** acknowledge a `PredictionScored` message only after all channel dispatches have completed or failed. | Must | Approved |

### 5.2 Confidence gate

| ID | Requirement | Priority | Status |
|---|---|---|---|
| `NTF-4` | The service **shall** discard a `PredictionMade` message whose `confidence` is strictly below `NOTIFICATION_MIN_CONFIDENCE`, without dispatching to any channel. | Must | Approved |
| `NTF-5` | The service **shall** acknowledge a discarded message cleanly, without incrementing its retry count. | Must | Approved |
| `NTF-6` | The service **shall** log a structured entry for every discarded message, including the `prediction_id`, `asset_id`, and the actual confidence value. | Must | Approved |

### 5.3 Notification message construction

| ID | Requirement | Priority | Status |
|---|---|---|---|
| `NTF-7` | The service **shall** resolve the asset's `name`, `expected_exchange`, and `provider_symbol` from the shared asset registry using the `asset_id` carried on the prediction. | Must | Approved |
| `NTF-8` | The service **shall** map `PredictionMade.magnitude` to a `signal_strength` string: `LARGE` → `HIGH`, `MEDIUM` → `MEDIUM`, `SMALL` → `LOW`. | Must | Approved |
| `NTF-9` | The service **shall** construct a `NotificationMessage` carrying: `company_name`, `exchange`, `ticker`, `direction`, `signal_strength`, `confidence`, and `decided_at`. | Must | Approved |
| `NTF-10` | The service **shall** fail the message, triggering the retry path, if the `asset_id` is not present in the registry. | Must | Approved |

### 5.4 Channel dispatch

| ID | Requirement | Priority | Status |
|---|---|---|---|
| `NTF-11` | The service **shall** dispatch the `NotificationMessage` to every registered channel concurrently. | Must | Approved |
| `NTF-12` | A failure in one channel **shall not** prevent the remaining channels from receiving the dispatch. | Must | Approved |
| `NTF-13` | The service **shall** log the outcome (success or failure) of each channel dispatch, including the `channel_id` and the number of recipients targeted. | Must | Approved |
| `NTF-14` | If every channel fails, the service **shall** treat the message as a processing failure and follow the retry path. | Should | Approved |

### 5.4a Verification result alert construction and dispatch

| ID | Requirement | Priority | Status |
|---|---|---|---|
| `NTF-41` | The service **shall** resolve asset display details from the shared registry using the `asset_id` carried on `PredictionScored`. | Must | Approved |
| `NTF-42` | The service **shall** construct a `VerificationMessage` carrying: `company_name`, `exchange`, `ticker`, `predicted_direction`, `actual_direction`, `predicted_magnitude`, `actual_magnitude`, `actual_return`, `confidence`, `is_correct`, and `scored_at`. | Must | Approved |
| `NTF-43` | The service **shall** dispatch the `VerificationMessage` to every registered channel without a confidence gate — every `PredictionScored` message triggers an alert. | Must | Approved |
| `NTF-44` | The service **shall** fail the message, triggering the retry path, if the `asset_id` is not present in the registry. | Must | Approved |
| `NTF-45` | If every channel fails dispatching a verification alert, the service **shall** treat the message as a processing failure and follow the retry path. | Should | Approved |

### 5.5 Channel extensibility

| ID | Requirement | Priority | Status |
|---|---|---|---|
| `NTF-15` | The service **shall** define a `NotificationChannel` Protocol with a `channel_id: str` property, an `async send(message: NotificationMessage) -> None` method, and an `async send_scored(message: VerificationMessage) -> None` method. | Must | Approved |
| `NTF-16` | The notification engine **shall** depend only on the `NotificationChannel` Protocol, with no reference to any concrete channel class. | Must | Approved |
| `NTF-17` | Adding a new channel **shall** require no changes to the engine — only implementing the Protocol and registering the instance. | Must | Approved |

### 5.6 Recipient management

| ID | Requirement | Priority | Status |
|---|---|---|---|
| `NTF-18` | The service **shall** load each channel's recipient list from a dedicated JSON file at startup. | Must | Approved |
| `NTF-19` | The recipient file for the email channel **shall** be `email_recipients.json`, containing a JSON array of email address strings. | Must | Approved |
| `NTF-20` | The recipient file for the WhatsApp channel **shall** be `whatsapp_recipients.json`, containing a JSON array of E.164 phone number strings. | Must | Approved |
| `NTF-21` | A channel whose recipient file is absent or contains an empty array **shall** be skipped at startup, with a warning logged. | Must | Approved |
| `NTF-22` | Recipient files **shall not** be re-read after startup; a restart is required to pick up changes. | Must | Approved |

### 5.7 Email channel

| ID | Requirement | Priority | Status |
|---|---|---|---|
| `NTF-23` | The email channel **shall** send one transactional email per recipient via the Brevo API. | Must | Approved |
| `NTF-24` | The email channel **shall** format the notification as a plain-text email body containing: company name, exchange and ticker, direction, signal strength, confidence (as a percentage), and decision time. | Must | Approved |
| `NTF-25` | The email channel **shall** set the email subject to a concise summary of the prediction, e.g. `[FEED ANALYZER] Lockheed Martin (NYSE: LMT) — UP / HIGH`. | Must | Approved |
| `NTF-26` | The email channel **shall** authenticate to Brevo using an API key supplied via environment variable, and **shall not** include the key in any log. | Must | Approved |

### 5.8 WhatsApp channel

| ID | Requirement | Priority | Status |
|---|---|---|---|
| `NTF-27` | The WhatsApp channel **shall** send one message per recipient via the Meta Cloud API. | Must | Approved |
| `NTF-28` | The WhatsApp channel **shall** format the notification as a short plain-text message containing: company name, exchange and ticker, direction, signal strength, confidence (as a percentage), and decision time. | Must | Approved |
| `NTF-29` | The WhatsApp channel **shall** authenticate via a Meta access token and phone number ID, both supplied via environment variables. | Must | Approved |
| `NTF-30` | The WhatsApp channel **shall not** include the access token in any log. | Must | Approved |

### 5.9 Operations

| ID | Requirement | Priority | Status |
|---|---|---|---|
| `NTF-31` | The service **shall** expose `GET /health` reporting process liveness. | Must | Approved |
| `NTF-32` | The service **shall** expose `GET /ready` reporting RabbitMQ consumer readiness. | Must | Approved |
| `NTF-33` | On shutdown the service **shall** stop consuming new messages, allow in-flight dispatches to complete or cancel safely, and close its connections. | Must | Approved |

## 6. Non-functional requirements

| ID | Requirement | Priority | Status |
|---|---|---|---|
| `NTF-34` | Logs **shall** include `prediction_id`, `asset_id`, `channel_id`, `correlation_id`, operation, and outcome on every dispatch event. | Must | Approved |
| `NTF-35` | Logs **shall not** contain API keys, access tokens, or recipient contact details. | Must | Approved |
| `NTF-36` | All secrets (Brevo API key, Meta access token, Meta phone number ID) **shall** be supplied only via environment variables and **shall not** be committed. | Must | Approved |
| `NTF-37` | The service **shall** pass `ruff check` and `mypy --strict`. | Must | Approved |
| `NTF-38` | Channel dispatches **shall** apply a per-call timeout so a slow external API cannot block the consumer indefinitely. | Must | Approved |

## 7. How it works

### 7.1 Consuming a prediction

**Purpose:** receive every prediction from the bus and decide whether to notify.

**Steps** → `NotificationEngine.handle_message`

1. A `PredictionMade` message arrives on `notification.predictions`.
2. Deserialise to a `PredictionMade` instance.
3. **Confidence gate:** if `message.confidence < NOTIFICATION_MIN_CONFIDENCE`, log at info level and
   ack the message. No channel is called.
4. Resolve asset details from the shared registry using `message.asset_id`. If the asset is unknown,
   raise `MessageProcessingError` to trigger the retry path.
5. Build a `NotificationMessage` (see §7.2).
6. Dispatch to all channels (see §7.3).
7. Ack the message.

### 7.2 Building the notification message

**Purpose:** produce a single neutral value object that every channel can format independently.

**Steps** → `NotificationEngine._build_notification`

1. Look up the asset record by `asset_id` in the registry. Extract `name`, `expected_exchange`, and
   `provider_symbol`.
2. Map `magnitude` to `signal_strength`: `LARGE` → `HIGH`, `MEDIUM` → `MEDIUM`, `SMALL` → `LOW`.
3. Construct `NotificationMessage(company_name, exchange, ticker, direction, signal_strength,
   confidence, decided_at)`.

**Rules:**

- `decided_at` is taken from `PredictionMade.decision_at`, already a UTC datetime.
- `confidence` is carried as a raw float `[0, 1]`; each channel is responsible for its own display
  formatting (e.g. `"72%"`).
- The `NotificationMessage` is immutable once built. Channels must not mutate it.

### 7.3 Channel dispatch

**Purpose:** deliver the notification through every active channel without one channel blocking another.

**Steps** → `NotificationEngine._dispatch`

1. Retrieve all channels from the `ChannelRegistry`.
2. Launch one coroutine per channel concurrently (`asyncio.gather(..., return_exceptions=True)`).
3. For each result:
   - **Success** → log at info with `channel_id` and recipient count.
   - **Exception** → log at error with `channel_id` and exception detail. Mark that channel as
     failed.
4. If at least one channel succeeded, the dispatch is considered successful.
5. If every channel failed, raise `MessageProcessingError` so the message follows the retry path.

**Rules:**

- Channels always run concurrently, never sequentially.
- No channel exception propagates to another channel.
- Each channel call is wrapped in a timeout (`NOTIFICATION_CHANNEL_TIMEOUT_SECONDS`).

### 7.2a Consuming a scored prediction

**Purpose:** receive every `PredictionScored` from the bus and dispatch a verification result alert.

**Steps** → `NotificationEngine.handle_scored`

1. A `PredictionScored` message arrives on `notification.scored`.
2. Deserialise to a `PredictionScored` instance.
3. No confidence gate — all scored predictions produce an alert.
4. Resolve asset details from the shared registry using `scored.asset_id`. If the asset is unknown, raise `MessageProcessingError`.
5. Build a `VerificationMessage` with: `company_name`, `exchange`, `ticker`, `predicted_direction`, `actual_direction`, `predicted_magnitude`, `actual_magnitude`, `actual_return`, `confidence`, `is_correct`, `scored_at`.
6. Dispatch to all channels via `_dispatch_scored` (same concurrency pattern as `_dispatch`).
7. Ack the message.

### 7.4 Email channel

**Purpose:** deliver a structured prediction alert to all email recipients via Brevo.

**Steps** → `EmailChannel.send`

1. Read the pre-loaded recipient list.
2. Format the subject: `[FEED ANALYZER] {company_name} ({exchange}: {ticker}) — {direction} / {signal_strength}`.
3. Format the plain-text body:

   ```
   Feed Analyzer — Prediction Alert

   Company  : {company_name} ({exchange}: {ticker})
   Direction: {direction}
   Signal   : {signal_strength}
   Confidence: {confidence_pct}%
   Decided  : {decided_at} UTC
   ```

4. For each recipient, POST to the Brevo `/v3/smtp/email` endpoint with the API key in the
   `api-key` header.
5. Log success or failure per recipient; do not abort the remaining recipients on a single failure.

### 7.5 WhatsApp channel

**Purpose:** deliver a concise prediction alert to all registered phone numbers via the Meta Cloud API.

**Steps** → `WhatsAppChannel.send`

1. Read the pre-loaded recipient list.
2. Format the message text:

   ```
   📈 Feed Analyzer Alert
   {company_name} ({exchange}: {ticker})
   Direction : {direction}
   Signal    : {signal_strength}
   Confidence: {confidence_pct}%
   Decided   : {decided_at} UTC
   ```

3. For each recipient, POST to the Meta Cloud API
   `/{phone_number_id}/messages` endpoint with the Bearer token in the `Authorization` header,
   sending a `type: text` message.
4. Log success or failure per recipient; do not abort the remaining recipients on a single failure.

**Rules:**

- Phone numbers in `whatsapp_recipients.json` must be in E.164 format (e.g. `+94771234567`). The
  channel validates the format at startup and rejects malformed entries with a warning.

### 7.4a Email channel — verification result alert

**Steps** → `EmailChannel.send_scored`

1. Read the pre-loaded recipient list.
2. Format the subject: `[FEED ANALYZER] {company_name} ({exchange}: {ticker}) — Verification CORRECT` or `— Verification WRONG`.
3. Format the plain-text body:

   ```
   Feed Analyzer — Verification Alert

   Company            : {company_name} ({exchange}: {ticker})
   Outcome            : CORRECT / WRONG
   Predicted Direction: {predicted_direction}
   Actual Direction   : {actual_direction}
   Predicted Magnitude: {predicted_magnitude}
   Actual Magnitude   : {actual_magnitude}
   Actual Return      : {actual_return_pct}%
   Confidence         : {confidence_pct}%
   Scored             : {scored_at} UTC
   ```

4. POST to the Brevo `/v3/smtp/email` endpoint per recipient.

### 7.5a WhatsApp channel — verification result alert

**Steps** → `WhatsAppChannel.send_scored`

1. Read the pre-loaded recipient list.
2. Format the message text:

   ```
   Feed Analyzer — Verification Alert
   {company_name} ({exchange}: {ticker})
   Outcome    : CORRECT / WRONG
   Predicted  : {predicted_direction} / {predicted_magnitude}
   Actual     : {actual_direction} / {actual_magnitude}
   Return     : {actual_return_pct}%
   Confidence : {confidence_pct}%
   Scored     : {scored_at} UTC
   ```

3. POST to the Meta Cloud API per recipient.

### 7.6 Recipient file loading

**Purpose:** supply each channel with its recipient list without reading the file on every message.

**Steps** → `RecipientLoader.load`

1. At startup, for each channel, resolve the path to its recipient file from configuration.
2. If the file is absent, log a warning and register an empty list for that channel.
3. If the file is present, parse it as a JSON array of strings.
4. If the array is empty, log a warning.
5. For the WhatsApp channel, validate each entry against the E.164 pattern
   (`^\+[1-9]\d{7,14}$`); discard and warn on any invalid entry.
6. Pass the loaded list to the channel constructor; the list is never re-read.

## 8. Interfaces

### 8.1 Messages consumed

| Queue | Binding key | Message | When |
|---|---|---|---|
| `notification.predictions` | `prediction.made` | `PredictionMade` | Every prediction emitted by the Prediction Service |
| `notification.scored` | `prediction.scored` | `PredictionScored` | Every scored prediction emitted by the Verification Service |

**`PredictionMade` fields used by this service:**

| Field | Used for |
|---|---|
| `prediction_id` | Log correlation |
| `correlation_id` | Log correlation, passed to channel logs |
| `asset_id` | Registry lookup for company name, exchange, ticker |
| `direction` | Notification content |
| `magnitude` | Mapped to `signal_strength` |
| `confidence` | Confidence gate; notification content |
| `decision_at` | Notification content (`decided_at`) |

**`PredictionScored` fields used by this service:**

| Field | Used for |
|---|---|
| `prediction_id` | Log correlation |
| `correlation_id` | Log correlation, passed to channel logs |
| `asset_id` | Registry lookup for company name, exchange, ticker |
| `predicted_direction` | Verification alert content |
| `actual_direction` | Verification alert content |
| `predicted_magnitude` | Verification alert content |
| `actual_magnitude` | Verification alert content |
| `actual_return` | Verification alert content (displayed as percentage) |
| `confidence` | Verification alert content |
| `is_correct` | Verification alert outcome label (`CORRECT` / `WRONG`) |
| `scored_at` | Verification alert content |

All other fields are forwarded unchanged to logs only.

### 8.2 Messages published

Not applicable — the Notification Service publishes no messages to the bus.

### 8.3 HTTP endpoints

| Method | Path | Purpose | Response |
|---|---|---|---|
| `GET` | `/health` | Process liveness | `200` with `{"status": "ok"}` |
| `GET` | `/ready` | RabbitMQ consumer readiness | `200` ready, `503` not ready |

### 8.4 Scheduled jobs

Not applicable — this service is entirely event-driven with no scheduled jobs.

### 8.5 External APIs

**Brevo — email channel:**

| Aspect | Detail |
|---|---|
| API endpoint | `POST https://api.brevo.com/v3/smtp/email` |
| Authentication | `api-key: {BREVO_API_KEY}` header |
| Free tier limit | 300 emails/day |
| Error handling | Non-2xx response → log error, continue to next recipient |

**Meta Cloud API — WhatsApp channel:**

| Aspect | Detail |
|---|---|
| API endpoint | `POST https://graph.facebook.com/v18.0/{META_PHONE_NUMBER_ID}/messages` |
| Authentication | `Authorization: Bearer {META_ACCESS_TOKEN}` header |
| Free tier limit | 1 000 conversations/month |
| Error handling | Non-2xx response → log error, continue to next recipient |

## 9. Data design

### 9.1 Owned schema

Not applicable — this service owns no database tables. Recipient lists are loaded from files.

### 9.2 Recipient files

Both files live under the path configured by `NOTIFICATION_RECIPIENTS_DIR` (default
`config/recipients/` relative to the service root).

**`email_recipients.json`**

```json
["alice@example.com", "bob@example.com"]
```

**`whatsapp_recipients.json`**

```json
["+94771234567", "+44701234567"]
```

Each file is a flat JSON array of strings. No other structure is present. Both files are
version-controlled; secrets (API keys, tokens) must never be added to these files.

## 10. Configuration

Read from the environment. Connection strings use their conventional unprefixed names.

### 10.1 Infrastructure

| Variable | Default | Effect |
|---|---|---|
| `RABBITMQ_URL` | `amqp://feed_user:local_dev_pw@localhost:5672/` | Broker connection |
| `LOG_LEVEL` | `INFO` | Structured log threshold |

### 10.2 Notification engine

| Variable | Default | Effect |
|---|---|---|
| `NOTIFICATION_PREDICTIONS_QUEUE` | `notification.predictions` | Queue for `PredictionMade` messages |
| `NOTIFICATION_SCORES_QUEUE` | `notification.scored` | Queue for `PredictionScored` messages |
| `NOTIFICATION_MIN_CONFIDENCE` | `0.6` | Predictions below this confidence are silently discarded (not applied to verification alerts) |
| `NOTIFICATION_CHANNEL_TIMEOUT_SECONDS` | `30.0` | Per-channel send timeout; exceeded → channel fails, others continue |
| `NOTIFICATION_RECIPIENTS_DIR` | `config/recipients` | Directory from which channel recipient files are loaded |

### 10.3 Email channel (Brevo)

| Variable | Default | Effect |
|---|---|---|
| `BREVO_API_KEY` | empty | **Secret.** Empty disables the email channel entirely |
| `BREVO_SENDER_EMAIL` | empty | From-address used on every email; must be a verified Brevo sender |
| `BREVO_SENDER_NAME` | `Feed Analyzer` | Display name shown alongside the from-address |

### 10.4 WhatsApp channel (Meta Cloud API)

| Variable | Default | Effect |
|---|---|---|
| `META_ACCESS_TOKEN` | empty | **Secret.** Empty disables the WhatsApp channel entirely |
| `META_PHONE_NUMBER_ID` | empty | The Meta Business phone number ID; required when the access token is set |
| `META_API_VERSION` | `v18.0` | Graph API version segment in the request URL |

## 11. Verification

> **None of these tests exist yet.** `src/services/notification/tests/` has not been created, so every
> row below states the test that *must* be written, not one that passes today. This is why the document
> status is `Approved`. Create the tests, then change the status to `Implemented` and set
> *Last verified against code* in section 1.

| Requirement | Method | Evidence |
|---|---|---|
| `NTF-1` | Test | `test_engine.py` — message consumed from `notification.predictions` |
| `NTF-2` | Test | `test_engine.py` — ack issued only after all dispatches settle |
| `NTF-3` | Test | `test_engine.py` — message nacked after max retries exceeded |
| `NTF-4`, `NTF-5` | Test | `test_engine.py` — message with confidence below threshold is acked, no channel called |
| `NTF-6` | Inspection | Structured log call in `NotificationEngine.handle_message` |
| `NTF-7`, `NTF-8`, `NTF-9` | Test | `test_engine.py` — `NotificationMessage` fields match registry and mapping |
| `NTF-10` | Test | `test_engine.py` — unknown `asset_id` triggers `MessageProcessingError` |
| `NTF-11`, `NTF-12` | Test | `test_engine.py` — channels called concurrently; first channel failure does not prevent second |
| `NTF-13` | Inspection | Log calls in `_dispatch` |
| `NTF-14` | Test | `test_engine.py` — all-channel failure raises `MessageProcessingError` |
| `NTF-15`, `NTF-16`, `NTF-17` | Inspection | `NotificationChannel` Protocol in `channels/base.py`; engine imports only the Protocol |
| `NTF-18`…`NTF-22` | Test | `test_recipients.py` — absent file, empty array, no re-read on second call |
| `NTF-20` | Test | `test_recipients.py` — E.164 validation rejects malformed numbers |
| `NTF-23`…`NTF-26` | Test | `test_email_channel.py` — Brevo POST called per recipient, subject/body format, key in header |
| `NTF-27`…`NTF-30` | Test | `test_whatsapp_channel.py` — Meta POST called per recipient, body format, token in header |
| `NTF-31`, `NTF-32` | Test | `test_integration.py` — health and readiness responses |
| `NTF-33` | Test | `test_integration.py` — graceful shutdown drains in-flight dispatches |
| `NTF-35`, `NTF-36` | Inspection | No contact details or secrets logged anywhere in the service |
| `NTF-37` | Demonstration | `ruff check` and `mypy --strict` pass |
| `NTF-38` | Test | `test_engine.py` — slow channel hits timeout, other channel still succeeds |

## 12. Failure handling

| Failure | Behaviour | Recovery |
|---|---|---|
| RabbitMQ unavailable at startup | Readiness fails; service waits for broker | Automatic on reconnection |
| RabbitMQ drops mid-run | Both consumers stop; messages remain queued | aio-pika reconnects; messages redelivered |
| `asset_id` not in registry (either message type) | `MessageProcessingError` raised; message follows retry path | Requires a registry or message fix |
| Prediction confidence below threshold | Message acked silently; no channel called | Not applicable — this is correct behaviour |
| `PredictionScored` received (any `is_correct` value) | Verification alert dispatched without a confidence gate | Not applicable — all scored predictions alert |
| Brevo returns non-2xx | Error logged for that recipient; remaining recipients still sent | No automatic retry per recipient; next message unaffected |
| Meta Cloud API returns non-2xx | Error logged for that recipient; remaining recipients still sent | No automatic retry per recipient; next message unaffected |
| All channels fail for a message | `MessageProcessingError` raised; message follows retry path | Retried up to max retries; then dead-lettered |
| One channel times out | That channel's send raises; error logged; other channels unaffected | Next dispatch starts fresh |
| Recipient file absent at startup | Warning logged; that channel registered with empty list and skipped | Requires a file fix and restart |
| Brevo daily limit reached | Brevo returns 4xx; logged as channel error | Resolved by the next UTC day or plan upgrade |
| Meta monthly limit reached | Meta returns 4xx; logged as channel error | Resolved when the monthly counter resets |

## 13. Assumptions, dependencies, and known limitations

### 13.1 Assumptions

- The shared asset registry (`assets.json`) contains a valid entry for every `asset_id` that the
  Prediction Service emits.
- Recipient files are maintained manually and committed to version control alongside the service.
- Brevo and Meta Cloud API free tiers are sufficient for the POC volume of predictions.
- All recipients within a channel receive the same message; per-recipient personalisation is not
  required.

### 13.2 Accepted design decisions

| Decision | Reason |
|---|---|
| File-based recipient lists instead of a database table | Simpler for POC; no migration infrastructure needed. Swap to a `notification.channel_recipients` table when runtime editing is required |
| No outbox / no notification history | This is alert delivery, not financial state. Best-effort with retry-on-failure is sufficient; a delivery audit log is deferred |
| `asyncio.gather` with `return_exceptions=True` for channel dispatch | Ensures one channel exception can never suppress another channel's attempt |
| Per-channel timeout rather than a shared one | Prevents a slow channel from holding up both the dispatch and the RabbitMQ ack |
| Brevo preferred over SendGrid or Resend for email | Most generous permanent free tier (300 emails/day vs 100); no trial expiry |
| Meta Cloud API preferred for WhatsApp | Official Meta API with 1000 free conversations/month; no third-party intermediary |

### 13.3 Known limitations

| Limitation | Consequence |
|---|---|
| Recipient lists are read at startup only | Adding a recipient requires a service restart |
| No per-recipient delivery tracking | There is no record of which recipients were successfully reached |
| Free tier caps on both channels | High prediction volumes could exhaust the daily/monthly limits; the service continues processing but later messages may not reach recipients |
| No deduplication of notifications | A superseded prediction (`supersedes_prediction_id` set) triggers its own notification; callers are not informed that an earlier prediction was revised |
| WhatsApp messages are plain text only | Meta Cloud API template messages require approval; free-form text is used for simplicity in this POC |

## 14. How to update this document

Follow the rules in [README.md](README.md#how-to-update-these-documents).

Component-specific notes:

- **Adding a channel** means a new class implementing `NotificationChannel`, a new recipient file,
  new configuration variables in section 10, new entries in sections 8.5, 11, 12, and 13, and a
  new binding in `infra/rabbitmq/definitions.json` only if a new queue is needed (existing channels
  share `notification.predictions`).
- **Changing the confidence gate default** (`NOTIFICATION_MIN_CONFIDENCE`) requires updating the
  default value in section 10.2 and re-checking `NTF-4` in section 11.
- **Changing the `NotificationMessage` shape** requires updating sections 7.2, 8.1, and both
  channel format descriptions in sections 7.4 and 7.5.
- **Adding a recipient** to a file requires a service restart — note this in your deployment steps.

## 15. Change history

| Date | Version | Change | Driver |
|---|---|---|---|
| `2026-08-05` | `1.0.0` | Initial specification | Notification service grooming session |
| `2026-08-06` | `1.1.0` | Added verification result alerts: `notification.scored` queue, `VerificationMessage`, `send_scored` protocol method, `NTF-39`–`NTF-45` | Verification alert feature |
