# LLM Browser Gateway verification record

Latest verification: 2026-10-09, Europe/Berlin, using the user's existing Chrome profile.
The Docker backend is deployed and healthy, but its post-deployment eight-request test **fails**
with six ChatGPT successes and two Claude Send-readiness errors. The earlier 0.1.6 host-mode
batch passed; the latest recurrence means Claude readiness is still intermittent.

## Docker Desktop deployment and verification

At **10:20 Europe/Berlin**, the gateway was built and deployed as `feed-llm-browser-gateway`
using the existing `infra/.env`. The image is `feed-analyzer/llm-browser-gateway:0.1`, with image
ID `sha256:ea5323aa79a1f3d012beaab9a3c8b8c204cd14dfae189daad879e37ee9f38920`.
The container is healthy, runs as `appuser`/UID 10001 with a read-only root, publishes only
`127.0.0.1:8091`, and uses `unless-stopped` restart. It connects to the existing `feed` database
and `llm_browser_gateway` schema. A preflight container verified configuration and database access.

The Windows backend was stopped only after confirming no active work. The saved extension 0.1.6
pairing reconnected automatically to Docker. `/health` and authenticated `/status` passed;
unauthenticated status returned 401. All ten pre-existing containers retained their IDs and start
times. No consuming application or extension code changed during deployment.

The post-deployment run `b8e7f492131e` at **10:20:43–10:22:09 Europe/Berlin** took **85.438 seconds**:

| Provider | Assigned requests | Successful | Failed |
|---|---:|---:|---:|
| ChatGPT | 6 | 6 | 0 |
| Claude | 2 | 0 | 2 |

Four occupied slots and four waiting requests were observed, without capacity violations or
monitoring errors. All eight request IDs stayed with one provider. Both Claude attempts failed
before submission at `send_available`: `509867bca635420c88f573b6913ef7b3` and
`fc1d66ef4039447192d678f8dd544817`. The extension remained connected, and no active/unknown attempts
remained. Claude's preserved UI retry time is **10:36:37 Europe/Berlin**. No cooldown was cleared or
advanced for this deployment test, and the failed batch was not retried.

The failing check remains
`tests/test_live_batch.py::test_eight_requests_share_four_slots_and_report_provider_counts`,
at its all-responses-successful assertion. Container health and ChatGPT execution pass; this does
not establish that containerization caused Claude's repeated website-readiness failure. The
background-page rendering hypothesis needs further evidence. The next proposed investigation is
bounded content-script diagnostics for document visibility, raw Send-control presence/visibility
and whether the activation lease was obtained; correlate these with the attempt before changing
the readiness behavior again. No additional speculative adapter change was applied during deployment.

The ignored runtime directory preserves `batch-integration-b8e7f492131e.json` and
`docker-deployment-latest.json`. Validation also passes 63 focused Python tests (two optional skips,
one Postgres deselection), 210 shared tests, Ruff, strict mypy and Compose validation against both
the real environment file and its tracked example. Operation commands are in
[SRS-11](../requirements/SRS-11-llm-browser-gateway.md#docker-desktop-deployment).

## Version 0.1.6 eight-request acceptance passed

Run `5e35b693e92f` completed at **10:08:18–10:08:47 Europe/Berlin**, after the user reloaded the
extension and the backend confirmed version 0.1.6 connected automatically. The unchanged live
acceptance test used eight simultaneous AsyncOpenAI calls through normal ChatGPT-then-Claude
routing, two slots per provider, and no SDK retries. No browser-inspection tool opened or focused
provider tabs during this run.

| Measurement | Observed result |
|---|---|
| Successful / failed requests | **8 / 0** |
| ChatGPT requests / successes | **2 / 2** |
| Claude requests / successes | **6 / 6** |
| Total batch duration | **29.0 seconds** |
| Peak occupied / submitted slots | **4 / 4**, two per provider |
| Waiting requests with all slots occupied | **4** |
| Slot reuse | Each Claude slot handled three requests |
| Providers per request | Exactly one for all eight observed request IDs |
| Capacity violations / observer errors | **0 / 0** |

Every response contained its unique expected answer. Pytest reported `1 passed in 29.48s`.
Final state: extension connected on 0.1.6, zero active requests/attempts and no provider cooldowns.
The complete machine-readable evidence is
`src/services/llm-browser-gateway/.runtime/batch-integration-5e35b693e92f.json`. This is one successful synthetic
acceptance batch, not a general website latency/reliability guarantee.

Before testing the repair, only the known pre-submit Claude `ui_changed` retry time from attempt
`f088c86891ee4190ad1c3b17e082bd39` was advanced from 10:15:24 to 10:08:16 Europe/Berlin. The
operation required a finished UI-failure attempt, the exact matching cooldown and no active or
unknown Claude attempts. No quota, login, verification or uncertain-submission state was cleared.

## Version 0.1.6 repair and regression evidence

Diagnostic run `ebee3d310c22` at **09:59:29–10:00:24 Europe/Berlin** reproduced the remaining
failure: four ChatGPT successes, three Claude successes and one Claude `ui_changed` error in
**54.687 seconds**. The inspected Claude slot completed three requests while its other slot
timed out before submission at `send_available`, retaining the test draft. Opening the failed tab
exposed an enabled Send control matching the existing selector. This supports a background-page
rendering/readiness issue; the exact website implementation is not assumed.

The repair requests a bounded, serialized activation only when Claude Send is still unavailable
in a hidden document after two seconds. It restores the previous tab after submission or failure
without overriding an intervening user switch. Generation remains concurrent. Details and bounds
are specified in [SRS-11](../requirements/SRS-11-llm-browser-gateway.md#73-browser-ownership-and-extension).
The regression fixture reproduces a hidden Send control that becomes visible only after tab
activation; it fails against 0.1.5 and passes with the repair. Worker tests cover serialization,
ownership, restoration, timeout, terminal failure, cancellation and disconnect.
Validation passes: TypeScript build, 25 extension tests, 58 focused Python tests (two optional
skips, one Postgres deselection), 210 shared tests, Ruff and strict mypy. The diagnostic run is
preserved as `.runtime/batch-integration-ebee3d310c22.json` under the gateway service.

The updated extension is built in `src/services/llm-browser-gateway/extension/dist`. The user
reloaded it for the passing run above; no backend restart or key re-entry was needed.

## Earlier eight-request rerun after routing correction

Run `9de46e86beff` completed at **09:33:09–09:34:14 Europe/Berlin** through normal HTTP routing,
using eight simultaneous AsyncOpenAI requests with SDK retries disabled. No provider state was
cleared and no routing configuration was changed. Existing cooldowns had expired naturally; the
test preflight now permits expired records while rejecting active or unknown cooldowns.

| Provider | Assigned requests | Successful responses | Failed requests |
|---|---:|---:|---:|
| ChatGPT | 6 | 6 | 0 |
| Claude | 2 | 0 | 2 |

The batch took **64.672 seconds**. Monitoring observed four occupied slots (two per provider),
four waiting requests and no capacity violations or monitoring errors. Each ChatGPT slot handled
three requests. All eight gateway request IDs were observed with exactly one provider, verifying
the routing correction in this live run. At most two submitted/generating attempts were observed.

Both Claude requests returned HTTP 503 `ui_changed` at `send_available`, before submission,
after 54.390 and 55.657 seconds end to end. They were not resubmitted to ChatGPT. The test passed
its provider-identity assertion and failed its all-responses-successful assertion, so full live
acceptance remains incomplete. The earlier proposed readiness repair below remains unapplied.

The extension remained connected and no request or attempt remained active at completion.
Claude's next eligible readiness check is **09:49:05 Europe/Berlin**; ChatGPT has no remaining
cooldown. This is a local readiness retry time, not an account quota reset.

The complete per-request results and final state are in
`src/services/llm-browser-gateway/.runtime/batch-integration-latest.json` (ignored by Git).

## Subsequent routing correction

The user clarified that one HTTP request must never be sent to multiple providers. The engine
now returns the first assigned provider's result, including readiness errors and failures before
or after submission. Availability is still persisted, so a separate new request or an unassigned
queued request can choose another provider. The old provider-attempt-count setting was removed;
this rule is enforced in code rather than relying on a configuration value of one.

The regression first reproduced the old behavior (ChatGPT reported a limit, then the same request
received Claude success). After the fix, 18 readiness/execution failure cases pass without touching
the second provider, while a distinct new request successfully selects it. A real loopback HTTP
test with the Python SDK confirms the first request receives its provider's 429 and only a new
request uses Claude. The full focused suite passes 58 tests, with the opt-in live batch and optional
LangChain checks skipped and Postgres tested separately. These routing checks use controlled
adapter outcomes; they do not resolve or waive the live page-readiness failure recorded below.

Active status records now include `request_id`. The live batch test accumulates the providers seen
for each request ID and fails on any cross-provider assignment. The historical batch report below
was produced before this correction and has not been rewritten as a passing result.
The updated backend was restarted and extension 0.1.5 reconnected automatically. No extension
reload was required; the previously recorded UI cooldowns were preserved.

## Initial eight-request full-gateway integration test (before routing correction)

`tests/test_live_batch.py::test_eight_requests_share_four_slots_and_report_provider_counts`
ran through the normal HTTP gateway at approximately **09:09:57–09:11:05 Europe/Berlin**.
It used real AsyncOpenAI calls, `browser-auto`, normal ChatGPT-then-Claude priority, no direct
adapter calls, no SDK retries and no configuration changes. It deliberately did not rerun the
failed batch or clear the resulting cooldowns.

| Measurement | Observed result |
|---|---|
| API requests sent together | 8 |
| Successful / failed API requests | **1 / 7** |
| Total batch duration | **68.235 seconds** |
| Maximum occupied slots | **4**, two per provider |
| Waiting requests while all four slots occupied | **4** |
| Maximum submitted/generating attempts observed | **1** |
| Capacity violations / monitoring failures | **0 / 0** |
| Slot reuse | Claude slot 0 handled two attempts |

| Provider | Routing attempts | Successful responses | Failed attempts |
|---|---:|---:|---:|
| ChatGPT | 2 | 0 | 2 |
| Claude | 3 | 1 | 2 |

Attempt counts were observed through `/status` and confirmed against the gateway's owned
Postgres attempt records. They are not eight independent requests: one request tried ChatGPT
then Claude, and four queued requests never acquired a slot before both providers became
unavailable. Request 8 succeeded through Claude in 17.859 seconds. Requests 1, 3 and 5 returned
HTTP 503 `ui_changed`; requests 2, 4, 6 and 7 returned HTTP 503 `browser_disconnected` because the
router had no eligible provider. The extension itself remained connected throughout the final
status check; that error code does not establish a WebSocket disconnection in this run.

The local machine-readable report, containing per-request timing and final state, is
`src/services/llm-browser-gateway/.runtime/batch-integration-9a5d1f7c52a3.json` (ignored by Git).
The opt-in test saves its report before asserting success, so a failed run remains measurable.
Normal test runs skip it without sending prompts; the live failure is not waived or weakened.

### Failure handoff

- **Expected:** all eight responses succeed, use at most four slots, and waiting work reuses them.
- **Observed:** capacity/queueing passed, but both ChatGPT probes failed at `readiness` after
  approximately 11–12 seconds. Two Claude executions failed at `send_available` before submission;
  one Claude execution succeeded. A later browser inspection found the ChatGPT composer visible
  with Chat selected, and Claude's draft present with Send visible and enabled; no alert was shown
  in the inspected Claude tab. No request remained active or uncertain after the batch.
- **Likely cause:** page/composer initialization under simultaneous multi-provider load; exact
  cause remains unconfirmed. Each request currently navigates once for its readiness probe and
  again for execution, increasing reload work. Earlier small passing samples did not cover this.
- **Concrete proposed repair, not applied:** retain a successfully prepared, owned tab between a
  request's probe and execution instead of navigating it twice. Make composer readiness account
  for the live page state within the request deadline, retain quota/login checks and the durable
  pre-Send acknowledgement, then rerun this unchanged eight-request acceptance test. Consider
  bounding simultaneous page preparation separately from concurrent generation if needed.

The repository's short failing-test investigation policy limits speculative fixes. This run
remains failed. Normal routing was left unchanged. Both providers have local `ui_changed`
cooldowns: next eligible checks **09:25:09** for ChatGPT and **09:26:05** for Claude, Europe/Berlin.
These are retry times, not account quota reset times.

## Version 0.1.5 repair and live verification

On the user's request to fix the failure, the adapter was changed to allow Claude up to 45 seconds
for Send readiness within the execution deadline. It reacquires the editor during that wait,
restores a replaced/cleared draft at most twice, and stops if unexpected nonempty text appears.
Quota, login and verification signals remain active during preparation. It rechecks the draft and
enabled Send after the durable acknowledgement, then clicks once. A persistently disabled Send
is a safe `temporary_unavailable` result with diagnostic `send_disabled`.

The build passes 15 extension tests, including seven content-script checks. The regression suite
first reproduced the old 15-second timeout and lost draft after editor replacement, then passed
with the fix. The focused Python suite passes 39 tests (one optional LangChain skip, one Postgres
deselection), plus Ruff and strict mypy.

The user loaded 0.1.5 and the backend confirmed that version connected automatically. Claude ran
alone in provider priority through the real AsyncOpenAI SDK with retries disabled. At approximately
08:56, two sequential requests passed in **12.6 seconds**, and two concurrent requests passed in
**8.6 seconds** (**1.47x** measured batch speedup). Both slots 0 and 1 were observed submitted at the
same time, and both returned their distinct exact expected answers. No cooldown or active attempt
remained afterward. The old known pre-submit `ui_changed` next-check time was advanced to allow
verification of the repaired adapter; no quota or uncertain-submission state was cleared.

ChatGPT's first 0.1.5 run passed two sequential requests in 38.8 seconds and concurrent slot 0,
but slot 1 hit a 30-second navigation timeout before a prompt was sent. Attempt
`55974621f9ca4925a905e5f72ad1c21c` returned `browser_disconnected`, `submitted=false`, diagnostic
`navigation`. The extension remained connected and no active/unknown attempts remained. Only this
known pre-submit transient's retry time was advanced for one diagnostic rerun; this initial
failure must not be counted as a passing batch.

The ChatGPT diagnostic rerun completed at approximately 09:00: two sequential requests passed in
**35.1 seconds**, then two concurrent requests passed in **21.2 seconds** (**1.66x** measured batch
speedup). Two overlapping submissions on slots 0 and 1 and distinct correct responses were
verified. This establishes successful concurrency for both adapters on 0.1.5, while the earlier
ChatGPT navigation timeout remains evidence of intermittent website/navigation readiness.
These small synthetic batches do not establish a general latency or reliability guarantee.

Normal ChatGPT-then-Claude priority was restored with two tabs per provider. The extension
reconnected automatically; the final status had no active attempts or provider cooldowns.

## Version 0.1.4 concurrent-tab verification

The updated backend applied its slot migration and reported `tabs_per_provider=2`. The extension
reconnected automatically after each backend restart. Each provider was tested alone through
`examples/concurrency_smoke.py`, using the real AsyncOpenAI SDK with retries disabled. Unique exact
answers identify each request and prevent crossed responses from counting as success.

| Provider | Two sequential requests | Two concurrent requests | Result |
|---|---|---|---|
| ChatGPT | 40.7 seconds | 22.0 seconds | Passed; 1.85x measured batch speedup, two overlapping submissions on slots 0 and 1, correct responses |
| Claude | 13.5 seconds on the second run | Failed before slot 1 submitted | Slot 0 returned the correct response; slot 1 returned HTTP 503 `ui_changed` at `send_available` |

ChatGPT completed at approximately 08:43; Claude's second run failed at approximately 08:45.
These are small synthetic batches, not general latency or throughput guarantees.

### Claude failure handoff

- **Failing check:** `examples/concurrency_smoke.py --provider claude`, concurrent request in
  `check()`; implementation in `extension/src/content.ts`, `run()` Send-readiness loop.
- **Observed versus expected:** expected both tabs to submit and return their distinct exact
  responses. Slot 1 retained an unsent draft and timed out waiting for an enabled Send control
  after 15 seconds. No uncertain submission or rate-limit signal was reported.
- **Evidence:** attempt `2c64f672fa0745aabe3b047478595828` ended `ui_changed`,
  `submitted=false`, stage `send_available`, after 19.953 seconds overall. Its exact tab remained
  at `https://claude.ai/new` with the test draft. The first run had the same pre-submit failure on
  slot 0, attempt `ce48e9720fc24f1197b24ee66a928fcb`. Browser inspection afterward showed the
  existing Send selector present and enabled. After confirming that first attempt was finished
  and unsent, only its `ui_changed` next-check time was advanced for one diagnostic rerun;
  no quota cooldown or unknown attempt was cleared. That rerun passed sequential requests but
  reproduced the failure on the newly opened second tab.
- **Likely cause:** timing between a newly loaded/background Claude composer and Send readiness;
  the exact trigger is unconfirmed. This does not establish that Claude rejects simultaneous
  generations, because the failing tab never submitted.
- **Suggested fix, not applied:** strengthen Claude's pre-submit readiness handshake to wait for
  the hydrated composer and enabled Send state within the overall request deadline. Add bounded
  diagnostics for button presence/enabled state and document visibility, then verify a cold
  background tab as well as an already-used tab. Preserve the durable acknowledgement before
  clicking Send and do not retry a possibly submitted generation.

Investigation stopped under the repository's roughly three-minute failing-test handoff policy.
No extension code or version was changed during this verification. Normal ChatGPT-then-Claude
priority was restored with two slots per provider and no active/unknown attempts. Claude's
remaining `ui_changed` cooldown schedules its next eligible probe for **09:00:45 Europe/Berlin**;
this is a local retry time, not a provider quota reset.

## Earlier version 0.1.3 compatibility results

Verified on 2026-10-09. The earlier compatibility failures were resolved for this sample suite;
these results do not imply that the new concurrent-tab check passed for Claude.

Each provider ran alone in the gateway priority configuration. The smoke script asserted the
provider on every response, so fallback could not mask a failing adapter. Requests used the real
OpenAI Python 1.109.1 SDK over loopback HTTP, with SDK retries disabled and no fake provider responses.

| Sample | ChatGPT | Claude |
|---|---|---|
| Exact text: `gateway works` | Passed | Passed |
| Strict JSON schema: `{"ok":true}` | Passed | Passed |
| Required function call with validated name and arguments | Passed | Passed |
| Tool-result follow-up reporting the supplied value `42` | Passed | Passed |
| AsyncOpenAI exact text: `async works` | Passed | Passed |
| Total for five sequential requests | **70.0 seconds** | **28.6 seconds** |

Claude completed at approximately 01:16; ChatGPT at approximately 01:18. These short samples
measure this run only and do not establish a general latency guarantee.

Commands from the repository root, restarting the backend with the matching single-provider
`BROWSER_GATEWAY_PRIORITY` before each run:

```powershell
.venv/Scripts/python.exe -u src/services/llm-browser-gateway/examples/sdk_smoke.py --provider claude
.venv/Scripts/python.exe -u src/services/llm-browser-gateway/examples/sdk_smoke.py --provider chatgpt
```

The backend was restored to its configured ChatGPT-then-Claude priority afterward. The extension
reconnected automatically across backend restarts and the update from 0.1.2 to 0.1.3, without key
re-entry. No consuming application was changed.

## Resolved failures

- Temporary pairing storage and stale popup status caused repeated manual reconnection. Version
  0.1.2 introduced restricted persistent storage, automatic reconnect and live status updates.
- ChatGPT's current response wrapper uses `data-markdown-text-style="assistant-message"` instead
  of the previous selector. Version 0.1.3 supports both wrappers. The earlier attempt
  `f8bc83cb289a45fda881d54d29064d9b` timed out at extraction despite producing the correct answer;
  it was reconciled only after that completed JSON was observed in its exact browser conversation.
  That recovery was not counted as a passing SDK request.
- Claude's Send readiness exceeded the old five-second window. Version 0.1.3 waits for composer
  hydration and allows a bounded 15-second Send wait. Its draft was confirmed unsent before retry.
- Literal fenced JSON extraction avoids website typography changing JSON quotation marks.

## Supporting checks and remaining coverage

The focused Python suite passed 37 checks, with the root-environment LangChain test skipped and
Postgres deselected. Separate earlier SDK-matrix and disposable-Postgres runs passed. The TypeScript
build and seven extension recovery tests passed, as did Ruff, strict mypy and 210 shared tests.

This proves the listed live samples. Named-tool and negative/refusal live scenarios remain in the
[remaining backlog](../backlog/E16-LLM-Browser-Gateway/README.md). Quota fallback is tested with simulated limits; real account
allowances were not deliberately exhausted. Complete stock-prediction or TradingAgents integration
is outside this service's scope.
