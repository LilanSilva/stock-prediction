# SRS-11: LLM Browser Gateway

## 1. Document control

| | |
|---|---|
| Document ID | `SRS-11` |
| Component | Standalone LLM Browser Gateway |
| Requirement ID prefix | `BGW` |
| Status | Implemented and Docker-deployed; latest live batch fails on intermittent Claude readiness; see the [verification record](../docs/llm-browser-gateway-verification.md) |
| Version | `1.10.3` |
| Source code | [llm-browser-gateway](../src/services/llm-browser-gateway/) |
| Tests | [tests](../src/services/llm-browser-gateway/tests/) |
| Last verified against code | `2026-10-10` |

## 2. Purpose and scope

### 2.1 What this component does

Accept a focused OpenAI Chat Completions HTTP contract, route each request to an available browser
adapter, and return its validated response. A custom Chrome extension uses the user's existing
logged-in ChatGPT, Claude, DeepSeek, Meta AI, Kimi or Gemini profile. The backend runs on Windows or in Docker Desktop; Chrome and
its extension stay in the Windows profile. PostgreSQL runs in Docker Desktop in both deployments.

### 2.2 In scope

- Text messages, JSON object/schema responses and client-executed function tools.
- Configured priority, persisted cooldowns and exclusive provider reservations; one provider per request.
- Shared compatibility logic inside the adapters and authenticated local browser transport.

### 2.3 Explicitly out of scope

- Configuring or changing stock-prediction consumers or TradingAgents.
- Responses API, SSE streaming, embeddings, audio/images/files, native provider API keys.
- Running client tools, managing provider accounts, solving verification challenges or bypassing limits.
- Multiple backend workers, multiple simultaneous Chrome profiles or internet exposure.

## 3. Definitions

| Term | Meaning |
|---|---|
| Adapter | Accepts original OpenAI request JSON and returns OpenAI response/error JSON plus separate execution status |
| Engine | Selects adapters and manages attempts; does not rewrite public request/response bodies |
| Pairing key | Local extension-to-backend credential, distinct from the SDK API key |
| Unknown submission | The backend cannot establish whether generation occurred; automatic resubmission is blocked |
| Reset time | Reliable, timezone-aware time exposed by the website; absent when unknown |
| Next check | Configured time to probe a provider again; not a claim about its real quota reset |

## 4. System context

### 4.1 Position in the pipeline

```text
SDK client -> localhost:8091 -> engine -> adapter/common -> authenticated WebSocket
                                                          -> Chrome extension -> LLM website
                          <-------------- validated response -------------------------
```

This is an independently started service. It has no RabbitMQ subscription or application pipeline
integration. The repository `shared` package supplies structured logging; OpenAI compatibility
belongs to the service-local `adapters/common` package.

### 4.2 Dependencies

| Dependency | Purpose | Failure impact |
|---|---|---|
| PostgreSQL | Availability and durable attempt ownership | Startup fails or requests return unavailable |
| Selected Chrome profile and unpacked extension | Website execution | Unavailable/unknown status depending on submission |
| ChatGPT / Claude / DeepSeek / Meta AI / Kimi / Gemini website | Generate answers | Provider-specific failure or cooldown |
| Root `.venv` and editable `shared` | Python runtime and logging | Service cannot start |

## 5. Functional requirements

| ID | Requirement | Priority | Status |
|---|---|---|---|
| `BGW-1` | The gateway **shall** accept the supported non-streaming Chat Completions contract. | Must | Implemented |
| `BGW-2` | The engine **shall** preserve original public request and adapter response bodies. | Must | Implemented |
| `BGW-3` | Shared adapter logic **shall** validate structured responses against supplied JSON Schema. | Must | Implemented |
| `BGW-4` | Shared adapter logic **shall** return validated function calls with IDs and JSON-string arguments. | Must | Implemented |
| `BGW-5` | The gateway **shall** select capable providers in configured priority order. | Must | Implemented |
| `BGW-6` | The gateway **shall** persist provider cooldowns across restarts. | Must | Implemented |
| `BGW-7` | The gateway **shall** block blind resubmission after an uncertain execution. | Must | Implemented |
| `BGW-8` | The extension **shall** recover recorded attempts without submitting a duplicate generation. | Must | Implemented |
| `BGW-9` | The ChatGPT adapter **shall** complete real text, structured JSON and tool/result cases in the paired profile. | Must | Implemented |
| `BGW-10` | The Claude adapter **shall** complete the same real browser acceptance cases. | Must | Implemented |
| `BGW-11` | The gateway **shall** return a terminal refusal without provider cycling. | Must | Implemented |
| `BGW-18` | Each HTTP request **shall** be assigned to at most one provider; readiness or execution failure **shall** return to that caller without dispatching the same request to another provider. | Must | Implemented |
| `BGW-20` | When all capable providers are blocked by persisted availability, the gateway **shall** return `429 rate_limited` if all reasons are quota limits, otherwise `503 temporary_unavailable`, without reporting a browser disconnect or dispatching work. | Must | Implemented |
| `BGW-21` | The Claude adapter **shall** identify the visible composer replacement notice “Your free messages return at” as a quota limit without inferring a timezone from its clock-only reset text. | Must | Implemented |
| `BGW-22` | A request's `invalid_output` or `invalid_request` result **shall** release its slot without creating or changing provider availability; startup **shall** remove legacy cooldowns with those reasons while preserving real availability blocks. | Must | Implemented |
| `BGW-23` | The ChatGPT reader **shall** wait for its answer's explicit turn-completion marker, or an observed Stop-control transition on older pages, within the request deadline before returning stable text. | Must | Implemented |
| `BGW-26` | An operator reset **shall** recover a missing content-script receiver by reloading only the owned provider tab's current URL, request Stop, and require explicit idle confirmation before clearing backend blocks. | Must | Implemented; fixture-verified |
| `BGW-27` | The DeepSeek UI adapter **shall** reuse shared OpenAI text/JSON/tool formatting, provider priority, cooldowns and slot isolation, and require completed final-answer evidence before returning a response. | Must | Implemented; fixtures and five-request live SDK acceptance passed |
| `BGW-28` | The Meta AI UI adapter **shall** reuse shared OpenAI formatting and routing, wait for explicit streaming completion, and extract complete JSON from Raw view instead of a collapsible tree. | Must | Implemented; fixtures passed, five-request live SDK acceptance passed on 0.1.18 |
| `BGW-29` | The Kimi UI adapter **shall** use existing OpenAI formatting and slot routing, distinguish Send/loading/Stop, and require its own completed-answer toolbar before returning stable text. | Must | Implemented; fixtures passed, five-request live SDK acceptance passed on 0.1.19 |
| `BGW-30` | The Gemini UI adapter **shall** reuse shared OpenAI formatting and slot routing, require explicit completion and its own response toolbar, and exclude Canvas content from answer extraction. | Must | Implemented; fixtures and five-request live SDK acceptance passed |

## 6. Non-functional requirements

| ID | Requirement | Priority | Status |
|---|---|---|---|
| `BGW-12` | The gateway **shall** authenticate SDK calls and allow only the configured extension origin. | Must | Implemented |
| `BGW-13` | The gateway **shall** bound request size, concurrent queued work and execution deadlines. | Must | Implemented |
| `BGW-14` | The gateway **shall** serialize attempts for each profile/provider/tab slot. | Must | Implemented |
| `BGW-15` | The gateway **shall** return responses parsed by real Python and JavaScript OpenAI SDKs over HTTP. | Must | Implemented |
| `BGW-16` | The gateway **shall** satisfy ChatOpenAI text, JSON-schema and function structured-output calls. | Must | Implemented |
| `BGW-17` | The gateway **shall** process independent requests in a bounded tab pool while sharing provider cooldowns. | Must | Implemented |
| `BGW-19` | The backend **shall** support Docker Desktop deployment configured from the existing `infra/.env`. | Must | Implemented |

## 7. How it works

### 7.1 Compatibility boundary

[app.py](../src/services/llm-browser-gateway/llm_browser_gateway/app.py) authenticates and limits the
body. [contract.py](../src/services/llm-browser-gateway/llm_browser_gateway/adapters/common/contract.py)
validates it without normalization. The engine receives the same object and passes it to the chosen
adapter with separate request ID, deadline, profile and capabilities. Shared
[formatting.py](../src/services/llm-browser-gateway/llm_browser_gateway/adapters/common/formatting.py)
renders history and parses one browser envelope into an OpenAI response. The engine returns that
body unchanged. Unsupported controls fail explicitly instead of being silently ignored.

All history is replayed in a fresh website conversation on each attempt. This includes previous
assistant calls and matching tool results. Website messages cannot reproduce native API role
isolation. Schema/tool enforcement happens after extraction. One fresh-conversation repair is
allowed inside the same deadline. Invalid output after repair and refusals are terminal.

Browser output is requested as one fenced JSON code block so website typography cannot replace
JSON delimiters with curly quotes. The adapter extracts the literal code content before validation.
The shared protocol also explains escaping quotes/backslashes inside string values, gives a valid
nested-JSON example, and reiterates the code fence for tool-result replies. Parsing remains strict.
Validation rejections log provider, attempt ID, repair flag, output character count and exception
class under `browser_output_rejected`, without prompt text, response text or validation messages.

### 7.2 Routing and recovery

[engine.py](../src/services/llm-browser-gateway/llm_browser_gateway/engine.py) filters capabilities,
skips cooldowns and atomically reserves a tab slot within a profile/provider. It probes readiness before execution.
The first successful reservation binds that HTTP request to one provider. A readiness or execution
failure is persisted and returned to the caller; the engine never dispatches that request to another
provider, regardless of submission status or retry-safety metadata. New or still-unassigned queued
requests can select another eligible provider when one is busy or on cooldown. A rate-limit message
does not trigger another attempt against that account immediately. One bounded output repair, when
needed, remains inside the already-selected provider.

If all capable providers are blocked, the engine returns `429 rate_limited` when every reason is a
quota limit, otherwise `503 temporary_unavailable`. Persisted UI, login or unknown-submission blocks
do not establish that the extension is disconnected. The rejection logs `browser_providers_unavailable`
with the request ID and provider reasons; authenticated `/status` exposes their next-check times.
Existing cooldowns are preserved, and the next request probes a provider once its check is due.
An actual readiness/transport disconnect still returns `browser_disconnected` for that attempt.

Each provider has a configurable pool of tab slots (default two). Requests reserve the first free
slot in provider-priority order; if all eligible slots are busy they wait inside the existing deadline
and queue bound. Each slot has a distinct persistent Chrome tab assignment and one active attempt.
Cooldowns remain per profile/provider, not per tab. Reservation checks and cooldown updates are
serialized in PostgreSQL, and a concurrent success cannot erase a newer cooldown. An uncertain
submission conservatively blocks new work for that provider until reconciled; other in-flight
requests retain their own slots. Public OpenAI payloads are unchanged.

`invalid_output` and `invalid_request` are request failures, not provider outages. They are recorded
on the finished attempt without changing provider availability. Startup removes legacy cooldowns
whose reason is one of those request failures after recovering unfinished attempts as unknown.
Quota, login, UI and unknown-submission blocks remain governed by the existing recovery rules.

The extension reports `submitting` and waits for a durable backend acknowledgment **before** clicking
Send. Disconnects and timeouts after dispatch are conservatively unknown. Such attempts retain
their reservation. Reconnect inventories existing page work or replays its cached terminal result;
it never submits the generation again. Restart converts unfinished reservations to unknown.
Operator reset first requests Stop and checks that owned tabs are idle, then clears the block.
Extension 0.1.10 recovers missing receivers after extension updates by reloading the same conversation
URL in an owned provider tab. It refuses to reload tabs that navigated outside their provider origin.
Reset then uses the UI cancellation path and waits up to five seconds for explicit idle
confirmation. Failure to inspect or stop a tab leaves backend state unchanged; no generation is sent.
Independent HTTP retries are independent requests; clients should disable automatic retries during
browser execution.

### 7.3 Browser ownership and extension

The Manifest V3 extension opens inactive gateway tabs in the installed Chrome profile. Claude's
bounded Send-recovery step can temporarily activate its owned tab as described below. Each execute
navigates its owned tab to the provider's new-conversation page. The popup can explicitly designate
the current ChatGPT/Claude tab; doing so authorizes replacing that tab's conversation. It validates
the origin before reuse. No provider cookies are exported.

Probes also navigate idle reserved tabs to load the current content script after extension reload.
Navigation waits for a new loading/complete event pair (up to 30 seconds), rather than using the
old document's ready state. A readiness probe has a maximum 45-second budget within the overall deadline.

The pairing key, profile and tab IDs persist in `chrome.storage.local`, restricted with
`TRUSTED_CONTEXTS` so content scripts cannot read them. Existing session pairing migrates on update;
matching quotes copied from `.env` values are removed. Unfinished job metadata stays in
`chrome.storage.session`; backend reservations still prevent duplicate work after Chrome restarts.
Pair once; the extension reconnects after browser/backend restarts, with authentication timeouts,
heartbeat checks and a reconnect alarm. The popup refreshes connection status automatically and
provides **Forget saved pairing** when no work is active or uncertain. The
content script selects normal Chat on the ChatGPT homepage, then uses provider selectors and
visible login, verification and quota detection. The current ChatGPT composer and Send selector
were inspected in the selected profile on 2026-10-09; generation acceptance remains separate.
Visible quota detection currently covers English notices and explicit offset-aware `datetime`
values. An unknown reset stays unknown; a default 15-minute probe cooldown is used instead.

Extension 0.1.7 also checks Claude's `[data-composer-stand-in]` notice, which replaces the editor
when the free allowance is exhausted. “Your free messages return at” reports `rate_limited` before
draft insertion or submission. A clock-only value such as “8:00 PM” leaves `reset_at` unknown.
Hidden notices and quota wording inside a user draft are not treated as provider limit signals.

ChatGPT response extraction supports both `data-message-author-role=assistant` and the newer
`data-markdown-text-style=assistant-message` content. Both providers wait for composer hydration;
Send readiness has a maximum 45-second budget for Claude and 15 seconds for ChatGPT, bounded by
the remaining content execution deadline. During this wait, quota/login/verification signals are
still checked. If hydration replaces or clears the editor, the adapter restores its own draft at
most twice; unexpected nonempty text stops the attempt instead of being overwritten. The live
editor payload and enabled Send control are checked again after the submission acknowledgement,
before clicking once. A Send control that remains disabled returns `temporary_unavailable`,
`submitted=false`, with diagnostic `send_disabled`; a missing control remains `ui_changed`.
These selectors and waits require live acceptance after website changes.

Extension 0.1.8 checks the ChatGPT answer's enclosing `[data-talvt-turn-state]` for `complete`.
A short pause or even valid interim JSON is insufficient while the turn is still generating. If
that marker is absent, the reader requires an observed Stop control to disappear; missing completion
evidence runs to the existing overall deadline and returns `submission_unknown`. A 60-second response
fits within the default 180-second budget, including navigation and queueing. The reader retains its
short final-text stability check after completion and never resubmits merely because generation pauses.
Claude's extraction logic is unchanged by this repair.

Extension 0.1.6 adds recovery for a background Claude page whose Send control has not become ready
after two seconds. The content script requests a temporary activation of its own reserved tab;
the worker serializes these requests, verifies ownership, origin, connection and preparation state,
and bounds both queue eligibility and the activation lease to ten seconds. It does not focus the
browser window. Activation is released after the single Send click, on failure/cancellation or
disconnect, or by the lease timeout. The previous tab is restored only if the gateway tab is still
active, preserving an intervening user switch. Quota checks, draft checks and the durable pre-Send
acknowledgement remain required; denied activation returns an unsubmitted failure. Generation
remains concurrent and the one-provider-per-request rule is unchanged. Build and regression tests
pass, as did the host-mode eight-request live acceptance rerun after extension reload. The later
Docker batch exposed recurring Claude readiness failures; see the
[verification record](../docs/llm-browser-gateway-verification.md).

Authenticated `/status` includes the connected extension's manifest version and `request_id` on
each active attempt, allowing the live batch test to detect cross-provider dispatch. Internal result logs
include a bounded execution-stage code (navigation, connection, readiness, editor, insertion, Send,
submission acknowledgment or extraction) without prompt/response content. These diagnostics do not
change public OpenAI responses.

### 7.4 DeepSeek UI adapter

Extension 0.1.13 includes `https://chat.deepseek.com/*` in its host permissions and content-script
matches, the `deepseek` tab resource and a popup designation button. The adapter uses the same
`BrowserAdapter`, shared compatibility library and UI execution pipeline. Engine logic is unchanged.
Registering it does not alter the default priority; add `deepseek` to `BROWSER_GATEWAY_PRIORITY` in
`infra/.env` to enable it. The suggested order is `chatgpt,claude,deepseek`. An older extension must
be reloaded before enabling DeepSeek. Website credentials remain in the selected Chrome profile.

The observed English composer is `textarea[placeholder='Message DeepSeek']`. Send and Stop are
unlabelled role-buttons: the reader distinguishes their observed arrow/square SVG glyphs within
`.ds-button--primary.ds-button--circle`, respects `ds-button--disabled`, and treats the loading
spinner as busy. Changed glyphs fail closed instead of guessing which button to click. Readiness
recognizes `/sign_in`, verification challenges and shared visible English quota notices. Quota
reset times are not guessed; unseen website notice variants still require live verification.

Only `.ds-assistant-message-main-content` is extracted, excluding DeepThink reasoning. A single
`.md-code-block pre` supplies raw JSON without its language/Copy/Download banner. The reader
requires a visible Read aloud control within that answer's `[data-virtual-list-item-key]` turn,
no Stop/loading control, and the existing final-text stability interval. Missing completion evidence
or cancellation after submission remains `submission_unknown`; no duplicate or cross-provider retry
is introduced. Text, JSON and tools use the same prompt/envelope protocol and `deepseek-web` identity.

### 7.5 Meta AI UI adapter

Extension 0.1.14 adds `https://www.meta.ai/*`, the `meta` provider and its popup tab designation.
The same browser adapter, shared formatter, cooldowns, exclusive slots and one-provider-per-request
rules apply without engine changes. Enable it by appending `meta` to `BROWSER_GATEWAY_PRIORITY`
after loading the extension and signing in. Suggested order: `chatgpt,gemini,claude,deepseek,meta`.
Credentials remain in Chrome; the adapter does not read tokens or make provider HTTP requests.

The hydrated editor is `[data-testid='composer-input'][contenteditable='true']`; the prehydration
textarea is deliberately ignored. Send and Stop use `composer-send-button` and `composer-stop-button`.
Extension 0.1.17 allows up to 30 seconds for Meta hydration, bounded by the supplied execution
budget. If its editor is still missing after two seconds in a hidden tab, the probe or execute
preparation requests the existing serialized ten-second activation lease for its owned tab.
It releases that lease on completion/failure, restoring the previous tab unless the user switched.
Probes carry their attempt ID so ownership and cancellation checks apply before activation. They
never insert or submit text. Other providers retain their existing hydration behavior.
Extension 0.1.18 uses the native input event emitted by the editing command for Meta, without
sending a second data-bearing event. It waits up to one second for the editor to commit the draft,
within the execution budget, before verifying the serialized payload and proceeding to Send.
The reader selects `assistant-message` only inside the `Meta AI response` article and requires both
`data-streaming-state="DONE"` and `data-streaming-complete="true"`, no Stop, and stable text.
For JSON tree output it clicks the answer's Raw control and reads `pre code`, preserving fields
hidden by tree collapse. Missing Raw or completion evidence never returns a partial answer.
Shared visible quota/verification notices and login routes retain existing failure semantics;
unobserved Meta-specific notice variants still require live verification. Responses identify
`meta-web`; structured JSON and client-executed tools use the existing shared prompt protocol.

### 7.6 Kimi UI adapter

Extension 0.1.15 adds `https://www.kimi.ai/*` (the observed redirect destination of `kimi.ai`),
the `kimi` provider and popup designation. It retains Meta AI support from 0.1.14. The existing
engine and shared OpenAI formatter are unchanged. Load the extension and sign in before appending
`kimi` to `BROWSER_GATEWAY_PRIORITY`; responses identify `kimi-web`.

Extension 0.1.19 shares Meta's native-input insertion path for Kimi's Lexical editor: the browser
editing command emits input once, and the adapter waits up to one second for the committed draft
within the execution budget before payload verification. It does not dispatch a duplicate event.

The observed editor is `.chat-input-editor[contenteditable='true']`. Send uses the `Send` SVG in
`.send-button-container`, excluding `.stop` and `.loading`, and respects CSS `.disabled`.
Stop uses the same container with `.stop` and the lowercase `stop` SVG; loading is busy.
The reader extracts the last assistant `.markdown`, with `pre code` for a single code block,
and requires a visible `Refresh` SVG in that answer's own `.segment-assistant-actions` toolbar,
no Stop/loading and stable text. The toolbar was absent during generation and present afterward.
Missing evidence remains unknown; cancellation requests Stop without sending another generation.
Shared login/verification/quota detection applies; unseen Kimi-specific notices need live verification.

### 7.7 Gemini UI adapter

Extension 0.1.16 adds `https://gemini.google.com/*`, the `gemini` provider and popup designation.
Fresh conversations use `/app`. The shared formatter, engine, cooldowns and exclusive slots remain
unchanged. After loading the extension and signing in, append `gemini` to `BROWSER_GATEWAY_PRIORITY`
to enable it. Responses identify `gemini-web`.

The editor is the contenteditable textbox labelled `Enter a prompt for Gemini`; Send and Stop use
`Send message` and `Stop response`. The reader selects `model-response message-content .markdown`
with an explicit `aria-busy` attribute, excluding embedded Canvas content. Completion requires
`aria-busy="false"`, a visible Copy button in that response's `message-actions`, no Stop, and stable
text. Redo is not reliable across response types. Single `pre code` blocks preserve literal JSON.
Missing completion evidence remains unknown, and cancellation requests Stop without resubmission.
Shared visible login/verification/quota detection applies; unseen Gemini-specific notices and
non-English selectors need live verification. Canvas-generated documents are outside this contract.

## 8. Interfaces

### 8.1 Messages consumed

No broker messages. `/bridge` receives authenticated extension heartbeats, progress, terminal job
reports and explicit operator reset requests.

### 8.2 Messages published

No broker messages. `/bridge` sends probe/execute/cancel commands with attempt IDs and acknowledgments.
The browser transport protocol is internal and separate from public OpenAI JSON.
Protocol version 2 requires a tab-slot identifier; incompatible older extensions are rejected before
dispatch to prevent concurrent requests accidentally sharing a tab. Extension 0.1.4 implements it.

### 8.3 HTTP endpoints

| Method | Path | Purpose | Response |
|---|---|---|---|
| `GET` | `/health` | Process liveness | `{"status":"ok"}` |
| `GET` | `/status` | Authenticated connection, priority, active attempts and cooldown metadata | `200` JSON |
| `POST` | `/v1/chat/completions` | Authenticated completion | `chat.completion` or OpenAI-shaped error |
| WebSocket | `/bridge` | Exact extension Origin plus first-message pairing authentication | Internal protocol |

SDK base URL: `http://127.0.0.1:8091/v1`. Use `Authorization: Bearer <gateway API key>` and
`Content-Type: application/json`. Completion query strings are rejected. Unknown routes return
404, wrong methods 405, invalid input 400, oversized bodies 413, all rate-limited 429, invalid
browser output 502, unavailable/uncertain work 503 and deadline exhaustion 504. Errors have
`error.message`, `error.type`, `error.param` and `error.code`. Completed engine responses include
`x-request-id`. No CORS wildcard is enabled.

| Request field | Supported behavior |
|---|---|
| `model` | Configured alias, default `browser-auto` |
| `messages` | 1–256 ordered `system`, `developer`, `user`, `assistant`, `tool` messages; text strings or text blocks |
| `response_format` | `text`, `json_object`, `json_schema`; optional schema `name`, `description`, `strict` |
| `tools` | Up to 64 function tools with unique names and object parameter schemas |
| `tool_choice` | `auto`, `none`, `required`, or a named function |
| `parallel_tool_calls` | Boolean; false restricts the returned batch to one call |
| `stream`, `n` | Omitted/false and omitted/1 respectively |
| `max_tokens`, `max_completion_tokens` | One positive integer ≤32768; browser instruction only, no measured token guarantee |

Function arguments in assistant history must be JSON strings and calls must have unique IDs and
matching tool results. Only text blocks are supported. Schemas use Draft 2020-12, bounded depth 32
and 5000 visited nodes, with resolvable local JSON Pointer references; remote references, schema IDs,
anchors and dynamic references are unsupported. Non-standard JSON constants are rejected.

Success includes `id`, `object="chat.completion"`, `created`, `model`, `choices[0]` and `usage=null`.
Model is the honest provider identity (`chatgpt-web`, `claude-web`, `deepseek-web`, `meta-web`, `kimi-web` or `gemini-web`), optionally with an observed
website model. Text/schema answers use `finish_reason="stop"`; tools use `"tool_calls"` and opaque
`call_...` IDs. Refusals populate `message.refusal`. Token counts are unavailable.

### 8.4 Scheduled jobs

| Job | Interval | Purpose |
|---|---|---|
| Extension heartbeat | 20 seconds | Maintain and detect bridge connection |
| Extension reconnect | 3 seconds after close; 1-minute alarm | Reconnect without resubmission |
| Retention cleanup | Backend startup | Remove finished metadata older than configured retention |

## 9. Data design

### 9.1 Owned schema

`llm_browser_gateway` in the configured database; normally local `feed`. Idempotent startup DDL:
[db.py](../src/services/llm-browser-gateway/llm_browser_gateway/db.py). No other service's tables are
written. One database advisory lock prevents multiple backend workers.

### 9.2 Tables

| Table | Columns | Constraints/purpose |
|---|---|---|
| `availability` | `profile_id`, `provider`, `reason`, `reset_at`, `next_check_at`, `updated_at` | Composite primary key `(profile_id,provider)`; UTC timestamps, nullable unknown reset/check |
| `attempts` | `attempt_id`, `request_id`, `profile_id`, `provider`, `slot`, `state`, `outcome`, `created_at`, `updated_at` | Attempt ID primary key; partial unique `(profile_id,provider,slot)` index for `reserved`, `submitted`, `unknown` |

No prompts, responses, cookies or credentials are stored in Postgres. Finished attempt metadata is
retained seven days by default. Unknown attempts remain until reconciled or explicitly reset.
Logs contain request/provider/outcome/elapsed metadata and never prompt/response bodies or keys.

## 10. Configuration

Settings read ignored `infra/.env`; environment variables override it. Prefix: `BROWSER_GATEWAY_`.

| Variable suffix | Default | Effect |
|---|---|---|
| `API_KEY` | Required | ≥24-character SDK Bearer credential |
| `PAIRING_KEY` | Required | Distinct ≥24-character extension credential |
| `DATABASE_URL` | Required | PostgreSQL DSN reachable from the backend; Compose supplies its service-network DSN |
| `EXTENSION_ID` | Required | Exact 32-character Chrome extension ID |
| `PROFILE_ID` | `default` | Must match popup profile name |
| `PRIORITY` | `chatgpt,claude` | Distinct registered providers in preference order; registered names are `chatgpt`, `claude`, `deepseek`, `meta`, `kimi`, `gemini` |
| `TABS_PER_PROVIDER` | `2` | Concurrent tab slots per provider, 1–4; tabs are created on demand |
| `MODEL_ALIAS` | `browser-auto` | Accepted client model name |
| `DEPLOYMENT` | `host` | `host` enforces loopback binding; `container` requires `0.0.0.0` internally and loopback-only port publication |
| `HOST` | `127.0.0.1` | Loopback in host mode; Compose sets `0.0.0.0` inside the container |
| `PORT` | `8091` | Packaged extension connects to this port; changing it also requires editing extension URL/CSP and rebuilding |
| `DEADLINE_SECONDS` | `180` | End-to-end execution deadline, 5–900 seconds |
| `QUEUE_SIZE` | `16` | Maximum admitted requests including active work, 1–1000 |
| `RETRY_SECONDS` | `900` | Next probe delay when a reliable reset is unavailable |
| `MAX_BODY_BYTES` | `262144` | HTTP body limit, 1024–2097152 bytes |
| `RETENTION_DAYS` | `7` | Finished-attempt metadata retention, 1–90 days |

### Windows setup and operation

1. Run the root setup script to synchronize the existing `.venv` and editable packages. Start the
   existing Docker Desktop PostgreSQL service. These instructions run the backend on the host;
   the next section describes the Docker alternative.
2. From `src/services/llm-browser-gateway/extension`, run `pnpm install --frozen-lockfile`, then
   `pnpm run build`. If Node needs the Windows certificate store, set `$env:NODE_USE_SYSTEM_CA='1'`.
3. In the selected Chrome profile, open `chrome://extensions`, enable Developer mode, choose
   **Load unpacked** and select `src/services/llm-browser-gateway/extension/dist`. Copy its ID.
4. Set the four required gateway secrets/ID in ignored `infra/.env`. Use separate random local
   credentials. Set `BROWSER_GATEWAY_DATABASE_URL` to the host-accessible existing `DATABASE_URL`
   value or another local database. Never commit actual values. Startup creates only its owned schema.
5. From the repository root run `.venv/Scripts/python.exe -m llm_browser_gateway`. Keep that
   terminal open; press Ctrl+C to stop. Run one process. No backend Docker container is required.
6. Open the extension popup, enter the configured pairing key and profile, and click **Connect this
   Chrome profile**. Status refreshes automatically. Log into ChatGPT and Claude normally in this
   profile. Default gateway tabs are separate; designation of a personal tab is optional.
7. Point a generic SDK to the base URL above, the local API key and model alias. Use a timeout greater
   than the gateway deadline (for example 210 seconds) and `max_retries=0`. No consuming application
   configuration is changed by this service.

After rebuilding extension code, click its **Reload** button in `chrome://extensions`; saved pairing
reconnects automatically. Pair again only after forgetting/uninstalling or changing credentials.
SDK latency follows website generation and can include queueing, a probe and one same-provider repair.
Measured sample latency is recorded in the [verification record](../docs/llm-browser-gateway-verification.md).
These short samples are not a latency guarantee; retain a client timeout above the overall gateway deadline.

### Docker Desktop deployment

The optional `llm-browser-gateway` Compose profile adds `feed-llm-browser-gateway`. Its
[Dockerfile](../src/services/llm-browser-gateway/Dockerfile) uses the repository's hash-verified
dependency lock and installs the shared package plus gateway backend. Chrome, extension files,
runtime reports and `.env` are not included in the image. No new database or volume is created.

Compose reads the existing `infra/.env` for interpolation and passes only the gateway variables
and log level to this service. `POSTGRES_USER`, `POSTGRES_PASSWORD` and `POSTGRES_DB` construct
the internal `feed-postgres:5432` connection, overriding the host-mode `BROWSER_GATEWAY_DATABASE_URL`.
The API key, pairing key and extension ID are unchanged. Required empty values fail startup
validation. Secrets are runtime environment variables, never image build arguments or copied files.

The container listens on `0.0.0.0:8091` internally and publishes **only `127.0.0.1:8091`** on
Windows, following [Docker's localhost port mapping](https://docs.docker.com/engine/network/port-publishing/).
Other containers on `feed-net` can reach the authenticated service directly. The host SDK URL and
extension WebSocket URL are unchanged. The service runs as UID 10001 with a read-only root,
temporary `/tmp`, dropped capabilities and `no-new-privileges`. `/health` verifies process
liveness; authenticated `/status` separately reports the browser connection and pending work.
Restart policy is `unless-stopped`; Chrome must still be open and logged in to process requests.

From the repository root, build while the current backend is still running:

```powershell
docker compose --env-file infra/.env -f infra/docker-compose.yml --profile llm-browser-gateway build feed-llm-browser-gateway
```

Before the first cutover, wait for `/status` to show no active requests or attempts, then stop the
Windows gateway process. Only one backend may hold the database advisory lock and port. Start
the container against the already-running PostgreSQL service without restarting dependencies:

```powershell
docker compose --env-file infra/.env -f infra/docker-compose.yml --profile llm-browser-gateway up -d --no-deps --wait feed-llm-browser-gateway
docker compose --env-file infra/.env -f infra/docker-compose.yml ps feed-llm-browser-gateway
docker compose --env-file infra/.env -f infra/docker-compose.yml logs --tail 30 feed-llm-browser-gateway
```

Rebuild and run the same `up` command for later backend updates, after draining active work.
The saved extension pairing reconnects automatically. Extension source changes still require its
separate build and Chrome reload. To return to host mode, stop this container first, then run the
host command above. Do not run `compose down` or remove database volumes for a gateway update.
Docker deployment does not configure either consuming application.

## 11. Verification

| Requirement | Method | Evidence |
|---|---|---|
| `BGW-1`, `BGW-3`, `BGW-4` | Test | `test_contract.py`, `test_http.py`: schema/tool validation, malformed JSON, unsupported controls, tool-result correlation |
| `BGW-2`, `BGW-5`, `BGW-7`, `BGW-11` | Test | `test_engine.py`: object identity, priority selection, unknown quarantine, refusal terminal, bounded repair |
| `BGW-18` | Test | `test_engine.py`: readiness/execution failures before and after submission never change provider; `test_socket_sdk.py`: real HTTP SDK error remains on its original provider, a separate new request selects the next one |
| `BGW-6`, `BGW-14` | Test | `test_postgres.py`: restart persistence, atomic reservation, uncertain reconciliation |
| `BGW-8` | Test | `extension/tests/background.test.mjs`: reconnect inventory, duplicate IDs, acknowledgment before submission |
| `BGW-12`, `BGW-13` | Test | `test_http.py`, `test_engine.py`: authentication/origin, unsupported requests and bounded queue |
| `BGW-15` | Test | `test_socket_sdk.py`, `test_http.py`: OpenAI Python sync/async and JavaScript SDK HTTP |
| `BGW-9`, `BGW-10` | Live demonstration | `examples/sdk_smoke.py --provider chatgpt` and `--provider claude`, both passed on 2026-10-09 with extension 0.1.3; [results](../docs/llm-browser-gateway-verification.md) |
| `BGW-16` | Test | `test_socket_sdk.py`: real ChatOpenAI text, JSON-schema and function structured-output HTTP with fake adapters |
| `BGW-17` | Test | `test_engine.py`, `test_postgres.py`, extension tests: concurrent slot isolation, queueing and provider cooldown preservation; single-provider timing through `examples/concurrency_smoke.py`, full-gateway capacity and routing through opt-in `tests/test_live_batch.py` |
| `BGW-19` | Test and live deployment | `test_config.py`: host loopback remains enforced, container binding requires explicit mode; Docker Compose validation, image build, container health and extension/SDK checks |
| `BGW-20` | Test | `test_engine.py::test_blocked_providers_are_unavailable_not_disconnected`: blocked reasons, no dispatch, preserved cooldowns and due-provider recovery; `test_http.py::test_connected_browser_with_provider_cooldowns_returns_unavailable`: authenticated WebSocket stays connected while the completion returns the correct error; `test_engine.py::test_all_limited_and_queue_bounds`: quota-only blocks remain 429 |
| `BGW-21` | Test and live DOM inspection | `extension/tests/content.test.mjs`: composer quota placeholder returns `rate_limited`, no inferred reset time or submission, hidden/unrelated notices and quoted draft text do not trigger quota detection; notice observed in the paired Claude profile on 2026-10-09 |
| `BGW-22` | Test | `test_engine.py::test_request_error_does_not_disable_provider`; `test_postgres.py::test_persistent_cooldown_restart_and_uncertain_reservation`: real SQL slot release, next-request admission, legacy cleanup and quota/unknown preservation |
| `BGW-23` | Test and DOM inspection | `extension/tests/content.test.mjs`: 65-second partial/valid-JSON pauses, missing completion evidence and legacy Stop transition; explicit completed-turn marker observed in the paired ChatGPT page |
| `BGW-26` | Test | `extension/tests/background.test.mjs`: missing-receiver recovery, unchanged conversation URL, foreign-origin rejection, and no backend reset without an explicit idle result |
| `BGW-27` | Test and live SDK/DOM verification | `test_deepseek.py`: SDK/engine/bridge/shared text, JSON and tool conversion with synthetic browser replies; extension tests: textarea entry, disabled Send, Stop, 65-second completion pause, missing completion, login/verification/quota, cancellation and isolated slots; 0.1.13 live exact text, JSON schema, required tool, tool-result and AsyncOpenAI checks passed in 21.7 seconds on 2026-10-09 |
| `BGW-28` | Test and live DOM inspection | `test_meta.py`: SDK/engine/bridge text, JSON and tool formatting after earlier provider cooldowns; extension fixtures: delayed completion, Raw tree extraction, missing evidence, disabled Send, Stop/cancellation, login/verification/quota and isolated slots; five-request live SDK acceptance passed on 0.1.18 |
| `BGW-29` | Test and live DOM inspection | `test_kimi.py`: SDK/engine/bridge formatting after earlier provider cooldowns; extension fixtures: 65-second pause, missing completion, disabled Send, Stop/cancellation, loading, login/verification/quota and isolated slots; five-request live SDK acceptance passed on 0.1.19 |
| `BGW-30` | Test and live DOM inspection | `test_gemini.py`: SDK/engine/bridge formatting after earlier provider cooldowns; extension fixtures: 65-second pause, missing completion/busy state, disabled Send, Stop/cancellation, login/verification/quota and isolated slots; five-request live SDK acceptance passed |

Repeatable verification commands are indexed in the service README. PostgreSQL tests require a
dedicated disposable database named **`llm_browser_gateway_test`**, using the credentials and
loopback host from `DATABASE_URL`. Create it once with local Postgres administration, set
`$env:BROWSER_GATEWAY_POSTGRES_TEST='1'`, and run:

```powershell
.venv/Scripts/python.exe -m pytest src/services/llm-browser-gateway/tests/test_postgres.py -q
```

That test resets the gateway schema **in the test database only**. It rejects non-loopback hosts.
SDK matrix verified on 2026-10-09: OpenAI Python 1.109.1 (root environment), OpenAI JavaScript 6.0.0,
and isolated `langchain-openai` 1.6.6 / `langchain-core` 1.6.9 / OpenAI Python 3.26.1. The latter uses
temporary packages with the existing interpreter, without changing root dependency pins.
Live synthetic checks, after pairing:

```powershell
.venv/Scripts/python.exe src/services/llm-browser-gateway/examples/sdk_smoke.py
```

Set provider priority to `chatgpt`, `claude`, `deepseek`, `meta`, `kimi` or `gemini` and restart to prove each separately. Simulate quotas
in routing tests; do not deliberately exhaust real account allowances. Test network disconnect and
manual recovery separately from ordinary completion. No live compatibility claim is made by a
fake-adapter test alone.

Pass `--provider chatgpt` (or `claude` / `deepseek` / `meta` / `kimi` / `gemini`) to the smoke script to assert single-provider routing and
the provider on every response. Add `--text-only` for the first diagnostic request. The full script
asserts exact text, structured JSON, function arguments, tool-result handling and async completion.

For queue throughput, set a single provider and run `examples/concurrency_smoke.py --provider
chatgpt` (or `claude` / `deepseek` / `meta` / `kimi` / `gemini`). It compares two sequential requests with two concurrent requests, validates
distinct expected answers and observes overlapping submissions on separate slots. Increase
`--requests` only for a deliberate larger batch. With extension 0.1.5, Claude passed in 12.6 seconds
sequentially versus 8.6 seconds concurrently (two requests; 1.47x measured batch speedup).
ChatGPT passed a diagnostic rerun in 35.1 seconds sequentially versus 21.2 seconds concurrently
(1.66x), after its initial batch hit a pre-submit navigation timeout. Both providers returned
correct distinct answers with overlapping submissions on two slots. See the
[live test evidence](../docs/llm-browser-gateway-verification.md) for both the transient
failure and successful runs; these samples do not establish a general performance guarantee.

For normal multi-provider routing, opt in with `BROWSER_GATEWAY_LIVE_BATCH_TEST=1` and run
`tests/test_live_batch.py` as documented in the service README. Eight AsyncOpenAI requests start
together against the public endpoint with SDK retries disabled. The test observes four occupied
slots and four waiting requests, limits each provider to two occupied slots, verifies slot reuse
and unique correct responses, and reports the successful response count per provider and each
request's end-to-end latency. It does not force an even distribution or change provider priority.
It also correlates observed attempts by gateway request ID and fails if any one request is assigned
to more than one provider. Preflight permits expired cooldown records so normal readiness probes
can run again; it rejects active or unknown cooldowns and never clears provider state.
The ignored `.runtime/batch-integration-latest.json` includes failures and final gateway state;
normal test runs skip this live check. Status polling verifies logical slot occupancy rather than
enumerating unrelated Chrome tabs.

The first full-gateway run on 2026-10-09 observed four occupied slots, four waiting requests,
two slots per provider and slot reuse without capacity violations, but failed end-to-end:
one successful Claude response and seven HTTP errors in 68.235 seconds. Under the earlier routing
behavior, ChatGPT had two routing attempts and Claude three (one request tried both). Readiness failures made both providers
unavailable to the remaining queue. That run failed despite the earlier smaller passing samples.

The rerun at 09:33 Europe/Berlin after the routing correction returned six successful ChatGPT
responses and two Claude HTTP 503 `ui_changed` errors in 64.672 seconds. All eight request IDs
stayed with exactly one provider. Four occupied slots, four waiting requests and slot reuse were
observed without capacity violations. Both Claude attempts failed at `send_available` before
submission; full live acceptance was still incomplete at that point. No cooldowns were cleared and the
extension was connected with no active attempts at completion.

Extension 0.1.6 subsequently passed the same eight-request acceptance test after the Claude
background-tab recovery repair. See the [current live evidence](../docs/llm-browser-gateway-verification.md)
for provider counts, timings, final state and the explicitly recorded pre-test UI-cooldown adjustment.
The preceding failed runs remain historical evidence; their results are not rewritten as passes.

The later Docker post-deployment batch exposed a recurrence of Claude's pre-submit Send-readiness
failure. Container health, database access and automatic extension reconnection passed. The latest
provider results and investigation handoff are recorded in the same verification record; the earlier
successful batch does not establish ongoing adapter reliability.

## 12. Failure handling

| Failure | Behavior | Recovery |
|---|---|---|
| Quota/rate limit | Persist cooldown and return 429 for this request; other requests may select another provider | Probe when due |
| Login/verification/UI changed | Persist unavailability and return the error to this caller; never redispatch this request | User restores session or selectors are updated; next probe |
| Disconnect before readiness | Safe unavailable result | Automatic reconnect using saved pairing |
| Submission uncertain/cancellation | Request Stop, preserve attempt ownership, no blind fallback | Late terminal reconciliation or explicit popup reset |
| Invalid JSON/tools | One bounded same-provider repair; then 502 for this request without disabling the provider | Later requests may use the same available provider; validation metadata is logged |
| Refusal | OpenAI refusal message, no fallback | Caller handles refusal |
| All providers on quota cooldown | 429 | Wait for next eligible check |
| All providers blocked, with any non-quota reason | 503 `temporary_unavailable`; log request ID and provider reasons without dispatch | Inspect `/status`; next request probes when a cooldown is due, while unknown submissions remain blocked |
| Queue full | 503 | Caller retries later as a new request |
| PostgreSQL unavailable | Fail closed | Restore database and restart if startup failed |

## 13. Assumptions, dependencies, and known limitations

### 13.1 Assumptions

- The user operates the selected logged-in browser profile and resolves login/verification prompts.
- Websites allow the installed extension to access their normal UI. UI selectors may change.
- The service uses one account per provider in one Chrome profile, without account/model quota splitting.

### 13.2 Known limitations

- Browser JSON/tool calls are prompted and validated emulation. Native tool execution, API-level
  role boundaries, deterministic controls, exact output-token limits and usage accounting are unavailable.
- `temperature`, `top_p`, stop sequences, log probabilities, provider extras and Responses API fail
  explicitly. Client defaults must stay within the supported fields; model alias and timeout need configuration.
- Whole-history replay consumes website context; requests exceeding the website's effective limits can fail.
- Quota detection depends on visible notices. The gateway never knows an undisclosed daily allowance.
- Both providers passed the targeted real Python SDK samples in the user's profile. Extended live
  negative-case checks remain in E16; consuming applications have not been configured or validated.
- A crash between browser completion and durable result handling may require operator reset. Unknown
  work is conservatively blocked rather than duplicated. Independent SDK retries have no exactly-once guarantee.

## 14. How to update this document

Follow [README.md](README.md#how-to-update-these-documents). Add providers through the adapter registry
and extension provider handlers while retaining the common contract. Update fields, statuses,
selectors, settings and acceptance evidence here in the same change. Mark live requirements
Implemented only after their real-provider cases pass; record outstanding acceptance in E16.

## 15. Change history

| Date | Version | Change | Driver |
|---|---|---|---|
| `2026-10-09` | `1.0.0` | Initial implementation specification; live acceptance pending | E16 |
| `2026-10-09` | `1.0.1` | Persistent pairing and reconnect; current website selectors/readiness; both live SDK sample suites passed | E16 live verification |
| `2026-10-09` | `1.1.0` | Configurable provider tab pools, slot isolation and shared cooldown concurrency protection; ChatGPT live concurrency passes, Claude pre-submit readiness failure recorded | Queue performance request |
| `2026-10-09` | `1.1.1` | Extension 0.1.5: bounded Claude Send readiness, hydration draft recovery, quota checks and single-submit regression tests; both live concurrency checks passed, with one ChatGPT pre-submit navigation timeout recorded | Claude concurrency failure |
| `2026-10-09` | `1.2.0` | Bind each HTTP request to one provider; remove configurable cross-provider retries; add failure regressions, real HTTP SDK proof and request-ID correlation in the live batch test | User routing correction |
| `2026-10-09` | `1.2.1` | Extension 0.1.6: bounded, serialized activation recovery for Claude Send preparation, automatic tab restoration and failure/cancellation tests; eight-request live acceptance passes | Claude background-tab readiness failure |
| `2026-10-09` | `1.3.0` | Docker Desktop backend deployment using infra/.env, explicit container binding, loopback publication, healthcheck and automatic restart | User deployment request |
| `2026-10-09` | `1.4.0` | Distinguish aggregate provider cooldowns from an actual browser disconnect and log routing rejections; extension 0.1.7 detects Claude's quota composer replacement | Misleading `503 browser_disconnected` while the extension was connected |
| `2026-10-09` | `1.5.0` | Extension 0.1.8 waits for ChatGPT completion; request validation failures no longer disable a provider; remove legacy invalid-output cooldowns and log bounded validation diagnostics | Completed browser answers followed by gateway 502/503 errors |
| `2026-10-09` | `1.6.1` | Extension 0.1.10 recovers stale content-script receivers during operator reset and waits for idle confirmation | Popup reported receiving end does not exist after extension reload |
| `2026-10-09` | `1.6.2` | Remove the unsuccessful HTTP/SSE experiment; extension 0.1.11 and backend use only the original UI adapters, preserving reset recovery | User requested UI-only rollback |
| `2026-10-09` | `1.7.0` | Add the DeepSeek UI adapter and Chrome host permission using existing shared formatting, routing and status contracts | User requested DeepSeek following the existing adapter style |
| `2026-10-09` | `1.8.0` | Add Meta AI UI adapter with explicit completion and Raw JSON extraction, reusing shared formatting and routing | User requested Meta AI adapter |
| `2026-10-09` | `1.9.0` | Add Kimi UI adapter with Send/Stop/loading and completed-turn detection, retaining Meta AI support | User requested Kimi adapter |
| `2026-10-09` | `1.10.0` | Add Gemini UI adapter with explicit busy-state and response-toolbar completion, excluding Canvas content | User requested Gemini adapter |
| `2026-10-09` | `1.10.1` | Extension 0.1.17: bounded Meta editor hydration and owned-tab activation recovery during readiness, with regression tests | Meta readiness failure before submission |
| `2026-10-09` | `1.10.2` | Extension 0.1.18: remove duplicate Meta input event and await native editor commit before verification | Live retest exposed duplicated Lexical draft |
| `2026-10-10` | `1.10.3` | Extension 0.1.19: use native input and await committed Kimi draft; clarify shared JSON string escaping; regression and five-request live SDK suite passed | Kimi failed input verification before submission |
