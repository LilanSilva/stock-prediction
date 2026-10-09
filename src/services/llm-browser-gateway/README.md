# LLM Browser Gateway

Standalone service for existing Windows Chrome ChatGPT, Claude, DeepSeek, Meta AI, Kimi and Gemini sessions. Implementation,
supported request fields, configuration, setup and recovery are documented in
[SRS-11](../../../requirements/SRS-11-llm-browser-gateway.md). Live acceptance is tracked in
the [verification record](../../../docs/llm-browser-gateway-verification.md).

| Path | Responsibility |
|---|---|
| `Dockerfile` | Backend image using the shared dependency lock and package |
| `llm_browser_gateway/app.py` | Authenticated HTTP routes and request lifecycle |
| `llm_browser_gateway/engine.py` | One-provider request assignment, bounded queue, deadlines and availability-based selection |
| `llm_browser_gateway/db.py` | Owned Postgres schema and reservations |
| `llm_browser_gateway/bridge.py` | Authenticated extension WebSocket and attempt correlation |
| `llm_browser_gateway/adapters/browser.py` | Registered browser adapters |
| `llm_browser_gateway/adapters/common/` | Shared OpenAI validation, prompt protocol, response formatting and statuses |
| `extension/src/background.ts` | Chrome bridge, tab ownership, reconnect and recovery |
| `extension/src/content.ts` | Website-specific UI execution for ChatGPT, Claude, DeepSeek, Meta AI, Kimi and Gemini |
| `extension/src/popup.ts` | Pairing and operator controls |
| `examples/sdk_smoke.py` | OpenAI/AsyncOpenAI JSON and tool-round-trip checks |
| `examples/concurrency_smoke.py` | Live sequential/concurrent timing and response-isolation checks |
| `examples/langchain_smoke.py` | Generic ChatOpenAI invocation and structured output |
| `tests/` | Contract, routing, SDK HTTP, bridge and opt-in persistence tests |
| `tests/test_live_batch.py` | Opt-in eight-request live gateway test: normal routing, four-slot capacity, queueing, response isolation and provider counts |

Use the repository's existing root `.venv`; do not create another environment. From the repository
root in PowerShell:

```powershell
.venv/Scripts/python.exe -m pytest src/services/llm-browser-gateway/tests -q -m 'not integration'
.venv/Scripts/python.exe -m ruff check src/services/llm-browser-gateway
.venv/Scripts/python.exe -m mypy --config-file src/services/llm-browser-gateway/pyproject.toml src/services/llm-browser-gateway/llm_browser_gateway src/services/llm-browser-gateway/tests
Push-Location src/services/llm-browser-gateway/extension
pnpm install --frozen-lockfile
pnpm run build
pnpm test
Pop-Location
```

SDK checks use fake adapters over real loopback HTTP. The optional LangChain test runs when
`langchain-openai` is available in the interpreter; it is not a service dependency. PostgreSQL
test setup and live smoke commands are in SRS-11 sections 10–11. Generated extension `dist/`,
dependencies and local runtime files are ignored by Git.

To verify the complete running gateway with eight simultaneous synthetic requests, keep normal
ChatGPT-then-Claude priority, two slots per provider, and no other gateway work or active provider
cooldowns. Expired cooldown records are allowed so normal gateway readiness checks can run again;
the test does not clear provider state. It does not change routing or call an adapter directly.
SDK retries are disabled; each request stays with its selected provider, including failures. New requests can select another
available provider. It records each request's response provider and elapsed
time, observes four occupied slots plus four waiting requests, checks slot reuse/capacity and
distinct correct responses, and saves `.runtime/batch-integration-latest.json`, including failures.
The request split is measured, not fixed at four per provider. Faster available slots may take more
queued work. Slot ownership is observed through `/status`; physical tab mapping is also covered by
the extension's slot-isolation tests.

```powershell
$env:BROWSER_GATEWAY_LIVE_BATCH_TEST='1'
try {
    .venv/Scripts/python.exe -m pytest src/services/llm-browser-gateway/tests/test_live_batch.py -q -s
} finally {
    Remove-Item Env:BROWSER_GATEWAY_LIVE_BATCH_TEST
}
```

Without that opt-in, the live test skips and sends no browser prompts.

Docker Desktop build, deployment with `infra/.env`, host-to-container cutover and recovery commands
are in [SRS-11: Docker Desktop deployment](../../../requirements/SRS-11-llm-browser-gateway.md#docker-desktop-deployment).
