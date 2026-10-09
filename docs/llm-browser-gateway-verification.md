# LLM Browser Gateway verification record

Latest verification: 2026-10-10, Europe/Berlin, using the user's existing Chrome profile.
The Docker backend is deployed and healthy with extension **0.1.19** confirmed connected. The earlier
fix addresses early ChatGPT response extraction and a request-error cooldown that disabled
ChatGPT for 15 minutes. Claude's genuine quota state is preserved. The latest full eight-request
acceptance run still fails as recorded below; targeted checks do not replace that acceptance run.
The 0.1.8 long-output test also failed on a bridge disconnect; that remains unresolved.

## Kimi input repair — 0.1.19 live SDK acceptance passed

The earlier Kimi attempt failed before submission at `input_verification`; inspection showed a
duplicated prompt in its Lexical editor. The adapter still dispatched a data-bearing synthetic input
after the editing command's native input, and checked before the editor committed the draft.
Version 0.1.19 reuses the corrected Meta insertion path for Kimi without changing routing,
completion checks or OpenAI formatting. The duplicate-input regression failed against 0.1.18,
then passed after the fix. TypeScript build and all **75 extension tests** passed. Disabled-Send
coverage allows the single polling interval introduced by commit waiting while still rejecting
early submission.

After 0.1.19 reload, the first live run passed text, strict JSON schema and required tool call.
The tool-result follow-up failed `502 invalid_output` after the one permitted repair. Both extracted
answers were 41 characters and raised `JSONDecodeError`; DOM inspection showed an unfenced answer
with unescaped nested quotes. The adapter's UI input and completion now worked, but that answer
was not valid JSON. Log: `.runtime/kimi-019-live.log`. The guarded command restored normal routing;
no availability block or unknown submission was created for this request failure.

The shared prompt protocol now explicitly explains JSON string escaping with a valid nested-object
example and reiterates fences for tool-result replies. Validation remains strict, with a regression
covering valid escaped text and rejection of the malformed equivalent. **84 backend tests** passed
(two skips, one integration suite deselected), as did Ruff. The Docker rebuild passed. Mypy remains unavailable under the previously recorded Windows
Application Control restriction.

The repeat suite passed all five live requests in **45.4 seconds**: exact text, strict JSON schema,
required function call, tool-result follow-up and AsyncOpenAI. Every response identified `kimi-web`;
SDK retries were disabled. Log: `.runtime/kimi-019-retest.log`. The earlier UI cooldown had already
expired, so no database adjustment was needed. The guarded run restored normal routing afterward.

Kimi was then enabled after Meta in `infra/.env`: `chatgpt,gemini,claude,deepseek,meta,kimi`.
Compose validation and gateway-only deployment passed. Final authenticated status confirmed extension
0.1.19 connected, this six-provider order, zero requests, no active attempts and no availability
blocks. No further Chrome reload was needed for the backend protocol update. The full mixed-provider
concurrency acceptance remains separate from this targeted suite.

## Meta readiness and input repair — 0.1.18 live SDK acceptance passed

The 0.1.16 failure occurred before input on execute navigation, after a successful separate probe.
DOM inspection confirmed both the hidden prehydration textarea and the later Lexical editor. The
old eight-second preparation window had no Meta background-activation recovery. Version 0.1.17
adds the bounded preparation behavior in SRS-11 without changing engine routing or OpenAI formatting.
Four regressions failed against 0.1.16 (background hydration, delayed hydration, shorter deadline,
and focus failure/cleanup), then passed after the repair. TypeScript build and all **73 extension
tests** passed, including owned-tab focus validation.

After reload, 0.1.17 passed preparation but failed before submission at `input_verification`:
attempt `a2c70e8e77b74800b7d6fa3ef18e4bb8`, 22:02:58 UTC. Inspection showed a duplicated draft.
The insertion command already emits input; the extra data-bearing synthetic event causes Lexical
to insert again, while the synchronous check can run before its commit. Version 0.1.18 removes that
extra event for Meta and waits for the native editor commit. A regression failed against 0.1.17 and
passed after the fix; build and all **74 extension tests** pass. The disabled-Send timing fixture
now allows one polling interval because commit waiting shifts polling by 50 ms; it still prohibits
an early click.

For the controlled 0.1.17 retest, only Meta's `ui_changed` next-check time was made due under the
provider advisory lock, after asserting no reserved/submitted/unknown Meta attempts. No quota or
unknown-submission block was altered. The guarded test restored `chatgpt,gemini,claude,deepseek`.
The captured log is `.runtime/meta-017-live.log`.

After 0.1.18 reload, the five live Python SDK checks passed in **47.7 seconds**, ending at
**22:06:39 UTC**: exact text, strict JSON schema, required function call, tool-result follow-up
and AsyncOpenAI. All responses identified `meta-web`. The same scoped eligibility check was used
for the earlier pre-submit UI cooldown; successful execution then cleared Meta availability normally.
`.runtime/meta-018-live.log` shows five distinct successful attempts and no bridge disconnect.
The guarded command restored normal routing before deployment.

Meta was then appended to the user's chosen order in `infra/.env`:
`chatgpt,gemini,claude,deepseek,meta`. Compose validation and gateway-only deployment passed.
Final status confirmed extension 0.1.18 connected, that order, zero requests, no active attempts and
no Meta cooldown. Kimi remains disabled with its previous failure unresolved. This targeted suite
does not replace the outstanding full mixed-provider concurrency acceptance.


## Gemini UI adapter — 0.1.16 live SDK acceptance passed

On 2026-10-09, the signed-in `gemini.google.com` UI returned the exact `GEMINI_UI_READY` JSON
code block. Longer output exposed Canvas content and showed that Redo is not a reliable completion
control. The adapter therefore reads only response markdown with an explicit `aria-busy` attribute,
requires its own completed Copy toolbar and absence of Stop, and excludes embedded Canvas markdown.
The engine and shared formatter are unchanged.

The TypeScript build, **68 extension tests**, **83 backend tests** (two skips, one integration suite
deselected) and Ruff passed. New fixtures cover a 65-second valid-JSON pause, missing completion/busy
state, disabled Send, Stop/cancellation, login/verification/quota, separate tab slots and real SDK
text/JSON/tool conversion using synthetic browser responses. Mypy remains unavailable because of
the previously recorded Windows Application Control restriction.

The user loaded 0.1.16 and authenticated status confirmed it connected. Docker build and deployment
passed. The five-request live OpenAI/AsyncOpenAI suite passed in **86.9 seconds**, ending at
**21:54:03 UTC**: exact text, strict JSON schema, required tool call, tool-result follow-up and async
text. All five responses reported `gemini-web`, with SDK retries disabled. The first request took
46.5 seconds; later requests took 9.1–10.8 seconds each. `.runtime/gemini-016-live.log` contains five
distinct successful attempts with no bridge disconnect during the run. The guarded test restored
the normal running Docker configuration afterward. This does not replace full batch acceptance.

After acceptance, `infra/.env` was updated to `chatgpt,claude,deepseek,gemini` and only the gateway
container was recreated. Compose validation and health passed. Final authenticated status confirmed
that order, extension 0.1.16 connected, two slots per provider, zero requests and no active attempts.
The disabled Meta/Kimi providers retain their pre-submit UI cooldowns described below.

## Meta AI and Kimi follow-up on 0.1.16 — pre-submit failures

After the combined extension reload, the guarded live SDK checks were attempted for both earlier
adapters. Meta returned `503 ui_changed`, diagnostic `readiness`, before sending its first prompt
(attempt `79c92c0739034185b850a9da6ceaaad1`, 21:54:46 UTC). Its probe had passed, but the execute
navigation's editor did not become ready within the preparation window. Kimi returned `503 ui_changed`,
diagnostic `input_verification`, also before submission (attempt `c0c110354af74098a44bc3464adcb2f5`,
21:55:02 UTC). Browser inspection showed the gateway draft duplicated in Kimi's editor; the exact
editor-event/hydration cause requires a focused repair. Neither suite reached its later JSON/tool
checks. The guarded command restored normal routing; no submitted or unknown attempts remained.

Both providers remain registered but excluded from normal priority. Their persisted `ui_changed`
cooldowns are preserved, not cleared manually. Logs are `.runtime/meta-016-live.log` and
`.runtime/kimi-016-live.log`. The older implementation records below describe fixture verification,
not successful live acceptance.

## Kimi UI adapter — 0.1.15 built; live SDK acceptance pending

On 2026-10-09, `kimi.ai` redirected to `www.kimi.ai` in the existing signed-in Chrome profile.
A synthetic UI prompt returned the exact `KIMI_UI_READY` JSON envelope. A longer response confirmed
loading and Stop controls, absence of the response's Refresh toolbar during generation, and its
presence after completion. Native `pre code` contained the expected JSON without header text.

The TypeScript build, **60 extension tests**, **80 backend tests** (two skips, one integration suite
deselected) and Ruff passed. New fixtures cover editor input, CSS-disabled Send, loading/Stop,
65-second completion pause, missing completion evidence, cancellation, login/verification/quota,
slot isolation and shared SDK text/JSON/tool formatting with earlier providers unavailable.
The combined 0.1.15 build retains Meta AI. Its reload replaces the earlier 0.1.14 activation request;
live SDK acceptance for both new providers remains pending. Python mypy remains blocked by the
previously observed Windows Application Control restriction. Docker build, Compose validation and
gateway-only deployment passed; normal routing remains `chatgpt,claude,deepseek` until the new
adapters pass live SDK acceptance. The last observed connected extension was still 0.1.13.

## Meta AI UI adapter — 0.1.14 built; live SDK acceptance pending

The signed-in website was inspected on 2026-10-09. A synthetic UI request returned valid JSON.
Observed controls include the hydrated composer, distinct Send/Stop test IDs, assistant-only message
container and explicit `DONE`/`true` completion attributes. The website initially displayed JSON
as a collapsible tree; switching its Raw control revealed complete `pre code` text. The adapter
uses that UI action instead of reading potentially collapsed tree content.

The TypeScript build, **52 extension tests**, **77 backend tests** (two skips, one integration
suite deselected) and Ruff passed. Fixtures cover completion pauses, Raw conversion, missing
completion/Raw, disabled Send, Stop/cancellation, login/verification/quota, slot isolation and shared
SDK text/JSON/tool conversion after earlier provider cooldowns. Python static checking remains
unavailable because Windows Application Control blocks the installed mypy binary module.
The engine and shared OpenAI formatter are unchanged. Docker build, Compose validation and
gateway-only deployment passed; existing `chatgpt,claude,deepseek` routing is preserved.
Live SDK acceptance awaits extension reload from 0.1.13 to 0.1.14 before enabling `meta`.

## DeepSeek UI adapter — 0.1.13 live SDK acceptance passed

The signed-in DeepSeek page was inspected on 2026-10-09. Direct UI checks verified the textarea,
CSS-disabled Send state, arrow/square glyph controls, a loading spinner, final-answer container and
its completed-turn Read aloud toolbar. The existing UI execution, shared OpenAI formatter and
engine are reused; the backend registers `deepseek` without engine changes.

The first live SDK run on 0.1.12 failed with `502 invalid_output`. The browser had returned valid
JSON, but DeepSeek renders code inside `<pre><span>` and the common `pre code` selector included
`jsonCopyDownload` banner text. A regression reproduced that exact prefix before the fix. Version
0.1.13 selects `.md-code-block pre` for DeepSeek only. **45 extension tests**, **74 backend tests**
(one optional SDK skip, two integration/live tests deselected), TypeScript build and Ruff passed.
The guarded live-test command restored normal provider priority after the failure.

DeepSeek fixture coverage includes a 65-second valid-JSON pause before completion, missing completion,
Stop, CSS-disabled Send, login/verification/quota, cancellation, code-block extraction, duplicate
protection and two independent slots. Real SDK/engine/bridge fixtures prove selection after earlier
provider cooldowns and shared text/JSON/tool formatting, using synthetic browser replies. These do
not replace the live SDK checks.

After the user activated 0.1.13, the live OpenAI SDK run passed exact text, structured JSON and
the required tool call, all reporting `deepseek-web`. The tool-result follow-up failed with
`503 submission_unknown`: attempt `18aa3e94b9e64c70a5ba047242cc89ff` started at 21:08:02 UTC and
became unknown at 21:08:05 UTC, well before the deadline. Browser inspection showed the completed
correct final answer containing `value=42`. The asynchronous fifth request was not reached.
Subsequent backend logs show repeated WebSocket reconnects through 21:08:53 UTC; the exact cause
of the original interruption is not established. The guarded test restored `chatgpt,claude`
priority automatically. DeepSeek was kept disabled in normal routing pending another test.

After the user completed the extension's operator reset, authenticated status confirmed no active
attempts or availability blocks. The retry at **21:12:06–21:12:27 UTC** passed all five live requests
in **21.7 seconds**: exact text, strict JSON schema, required tool call, tool-result follow-up and
AsyncOpenAI. Each SDK response reported `deepseek-web`; SDK retries were disabled. The captured
`.runtime/deepseek-013-retry.log` shows successful extraction for all five distinct attempts and no
bridge disconnect during the run. This success does not establish the earlier disconnect's cause.

The ignored `infra/.env` now explicitly sets `BROWSER_GATEWAY_PRIORITY=chatgpt,claude,deepseek`.
Only the gateway container was recreated; Compose validation and health passed. Final authenticated
status confirmed extension 0.1.13 connected, that priority order, two slots per provider, zero requests,
no active attempts and no availability blocks. No extension reload was needed for this configuration
change. This single-provider SDK acceptance does not replace the outstanding full batch acceptance.

## UI-only rollback — extension 0.1.11 deployed and live smoke passed

At the user's request, the unsuccessful HTTP/SSE experiment was removed from source, configuration,
status fields, extension packaging and its dedicated tests/examples. The earlier live experiment
returned HTTP 403. ChatGPT and Claude now use only the existing UI adapters. Reset receiver recovery
and earlier UI completion fixes are retained. The backend was rebuilt and deployed using `infra/.env`;
Chrome reported 0.1.11 connected. The packaged extension contains only the background, UI content and
popup scripts. No provider HTTP/SSE scripts or transport switch remain.

The TypeScript build, **36 extension tests**, **71 backend tests** (one optional SDK skip, two
integration/live tests deselected), Ruff and Compose validation passed. All 21 relative documentation
links/anchors resolved. Windows Application Control blocked importing mypy's binary module, so the
type check could not run in this verification.

One real OpenAI SDK request through the restored ChatGPT UI adapter returned the exact unique test
marker in **16.5 seconds**, model `chatgpt-web`, request `7d5a79196f7e4b31bf1c715f5671a1ed`, with SDK
retries disabled. Final status showed extension 0.1.11 connected, zero requests, no active attempts
and no availability blocks. This targeted smoke does not replace the unresolved full batch acceptance.

## Extension 0.1.10 operator-reset recovery — block cleared, acknowledgement unresolved

The user reported `Connected; 0 pending jobs (v0.1.9)` followed by
`Could not establish connection. Receiving end does not exist.` when invoking reset. The backend
still held the earlier unknown attempt. The reset implementation contacted saved tabs without
recovering scripts invalidated by an extension update. A fixture reproduced the failure before the fix.

The corrected reset restores the script by reloading the same owned conversation, sends the
UI stop command, and requires idle confirmation before requesting backend reset.
No generation is sent. Foreign-origin tabs are not reloaded. The user activated 0.1.10 and reported
`Gateway did not acknowledge; no submission allowed`. Authenticated backend status subsequently
confirmed 0.1.10 connected, zero requests, no active attempts and no availability rows: the old block
was cleared. Logs show repeated WebSocket reconnects around 22:31 Europe/Berlin, but do not establish
why the reset acknowledgement was missed. The popup confirmation failure remains unresolved.
The TypeScript build and all **45 extension tests** passed, including missing-receiver recovery,
foreign-origin rejection, and rejection of an unverified idle state.

## Extension 0.1.8 completion and request-error recovery — 18:06 deployment

The 0.1.7 service logs showed successful ChatGPT requests, including one taking **50.803 seconds**,
followed by request `ae48d7e0f07746279e5ff69eac111266` ending in `invalid_output` after one
same-provider repair at **17:52:11 Europe/Berlin**. PostgreSQL then blocked ChatGPT for 15 minutes.
With Claude already quota-limited, later requests returned `503 temporary_unavailable` without
dispatch. An invalid response is a request failure and should not disable the provider.

The old browser reader could accept text that paused for 1.5 seconds when no recognised Stop
control was visible. A regression fixture reproduces this with a 65-second generation pause.
The completed ChatGPT page inspected in the user's profile contained valid JSON and an explicit
`data-talvt-turn-state="complete"` ancestor. Logs did not retain the rejected output, so they do not
establish its exact parsing failure. The new reader requires completion evidence before returning
stable text, within the existing overall 180-second deadline. It also supports an observed Stop
control disappearing on older pages, and conservatively reports unknown submission if no completion
evidence arrives before the deadline.

The backend now records `invalid_output`/`invalid_request` on the attempt and releases its slot
without changing provider availability. Startup removes legacy cooldowns with these reasons after
recovering unfinished work. Validation failures log only provider, attempt ID, repair flag, character
count and exception class; prompts, answers and validation messages are not logged.

Only `feed-llm-browser-gateway` was rebuilt and recreated using `infra/.env`, while idle. The new
container started at **18:06:53 Europe/Berlin**, image ID
`sha256:392e37c711c750cb10437404428e148ce8f1f42f47f939b436550275922a7722`.
Health passed and extension 0.1.8 reconnected automatically. Authenticated status confirmed the
legacy ChatGPT `invalid_output` block was removed and Claude's `rate_limited` row was unchanged.

Regression checks passed: **71 gateway tests, 217 shared tests, 33 extension tests**, TypeScript
build, Ruff and strict mypy (23 Python files). One optional gateway SDK test skipped; the standard
run excluded the two opt-in suites. The separate real PostgreSQL integration test passed against
the dedicated disposable `llm_browser_gateway_test` database, including request-error slot reuse,
legacy cleanup, and preservation of concurrent quota and unknown-submission blocks. The new
generation-pause and provider-availability regressions were observed failing before their fixes.

The live run `60444f61c05e` at **18:07:56–18:09:27 Europe/Berlin** used two normal-routing SDK
requests with retries disabled. The named tool call passed in **15.813 seconds**. The 60-item
structured-output request failed after **90.922 seconds** with `503 submission_unknown`, correlated
with a bridge `WebSocketDisconnect` at 18:09:27, before the 180-second deadline. The extension
reconnected automatically at 18:09:28. Both requests stayed on ChatGPT; its failed attempt
`bf50a2066da24466b28f95df282442e9` remained unknown. This is not evidence that rendering caused the
disconnect. The saved `.runtime/extension-018-verification.json` records the failure. Investigation
was interrupted while checking connection recovery, and the user then requested the separate
network experiment. The existing UI implementation was retained.

## Extension 0.1.7 live activation and quota verification — 17:48

After the user reloaded the extension, the backend confirmed **0.1.7** connected at
**17:46:55 Europe/Berlin**. A three-request normal-routing SDK check ran at **17:48:23–17:48:47**,
with two slots per provider, unchanged priority, and SDK retries disabled. Claude's previous
cooldown had expired naturally; no provider state was cleared or advanced.

| Provider | Requests | Observed result | Duration |
|---|---:|---|---:|
| ChatGPT | 2 concurrent | Both HTTP 200 with their distinct exact expected answers | 23.719 s / 23.734 s |
| Claude | 1 | HTTP 429 `rate_limited` during readiness, before submission | 7.688 s |

The Claude result proves the updated detector recognises the live quota notice. It does not prove
Claude generation while the account is limited. Its persisted reason changed from `ui_changed`
to `rate_limited`, with `reset_at` unknown and next eligible check **18:03:30 Europe/Berlin**.
Each of the three request IDs was observed on exactly one provider, without observer errors.
Final state: extension 0.1.7 connected, zero active requests/attempts, and ChatGPT available.
The full batch took **24.25 seconds**; the local `.runtime/extension-017-verification.json`
preserves run `44dab6884c29`. This targeted check does not replace the full eight-request
acceptance test recorded below.

## Misleading disconnect error and Claude quota detection — 16:21

The reported `503 browser_disconnected` was reproduced while `/status` showed extension 0.1.6
connected. The container had no connection-close event since its 10:20 startup. Both providers
had persisted `ui_changed` cooldowns: Claude after a 16:02 readiness failure and ChatGPT after a
16:04 pre-submit Send failure. The engine's no-eligible-provider branch incorrectly labelled
that state as a browser disconnect.

The corrected backend was deployed at **16:16:41 Europe/Berlin** using the existing `infra/.env`;
only `feed-llm-browser-gateway` was recreated. Image ID:
`sha256:9e9ade12e589045dde538fadbe3d5f385bd0689caf9ce7e5f904f5508f609e93`.
Container health passed, and the saved extension pairing reconnected automatically at 16:16:46.
At 16:17:00 the same cooldown state returned `503 temporary_unavailable`, request
`144b2a0615b047e48cc9e2497ef76c09`, with both provider reasons recorded in the new routing log.

Normal OpenAI SDK samples used unchanged provider priority and disabled SDK retries:

| Time, Europe/Berlin | Result | Duration |
|---|---|---:|
| 16:17:25 | Claude selected after its check became due; 503 `ui_changed` at readiness, before submission | 13.188 s |
| 16:20:39 | ChatGPT selected after its cooldown expired; HTTP 200 with the exact unique expected answer | 21.359 s |

ChatGPT request `1b727b9b2e7e448e984d8ee62ad75080` completed with the extension connected and
zero active work. No cooldown was cleared or advanced. Claude's next recorded check remained
16:32:38; these are local probe times, not provider quota reset times.

Read-only inspection of the failed Claude tab found **“Your free messages return at 8:00 PM.”**
inside `section[data-composer-stand-in]`, replacing the editor. This explains the current Claude
readiness failure; it does not establish the cause of earlier Send-readiness failures. Extension
**0.1.7** now recognises this notice as `rate_limited`. Its clock-only text does not establish a
timezone-aware reset, so the existing bounded probe schedule remains. Activation and live detector
verification subsequently passed as recorded above.

Validation: five Python regressions failed before the router fix and pass afterward; the quota
fixture likewise failed before the selector fix and passes afterward. The full targeted checks pass
**68 gateway tests, 217 shared tests, 29 extension tests**, TypeScript build, Ruff and strict mypy
(23 Python files). One optional SDK test skipped; live/disposable-database suites were not enabled.
The local `.runtime/cooldown-error-fix.json` preserves the HTTP results and state snapshots.
The behavioral contract is in [SRS-11](../requirements/SRS-11-llm-browser-gateway.md).

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

This proves the listed live samples. Named-tool and negative/refusal live scenarios remain
unverified. Quota fallback is tested with simulated limits; real account
allowances were not deliberately exhausted. Complete stock-prediction or TradingAgents integration
is outside this service's scope.
