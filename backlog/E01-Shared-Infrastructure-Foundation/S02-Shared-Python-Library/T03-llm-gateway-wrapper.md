# T03: Provider-Configurable LLM Gateway Wrapper

> **POC-6 finding override (2026-07-13):** Prediction-time KG-plus-LLM arbitration is blocked for M1. The shared LLM gateway is still required, but M1 service usage is limited to Cleansing Service ambiguous extraction/factual-conflict resolution.

## Context

This task builds the LLM gateway in `src/shared/llm/`. The gateway centralizes all LLM calls so services do not depend directly on one provider SDK, one model, or one API-key format.

The wrapper must support provider/model selection through configuration. For example, the project may use OpenAI, Anthropic, Bedrock, or another approved provider later by changing environment variables and adding/choosing the matching adapter.

## Design Goal

Service code calls one stable interface:

```python
result = await llm_gateway.complete_structured(
    task="cleansing_merge",
    prompt_version="cleansing-merge-v1",
    messages=messages,
    output_schema=MergeArticlesResult.model_json_schema(),
    correlation_id=correlation_id,
    cache_key=input_hash,
)
```

The gateway handles provider differences:

- API key or cloud credential lookup
- model ID
- structured-output mode or schema/tool emulation
- timeout and retry policy
- token usage extraction
- cache identity
- cost metadata
- provider/model response hash

## M1 Usage Rule

For M1:

- Cleansing Service may call the gateway only for ambiguous extraction, merge, or factual-conflict resolution.
- Prediction Service must not call the gateway.
- Future prediction arbitration may reuse this gateway only after a new controlled hypothesis is approved.

## Configuration

```python
class LLMSettings(BaseSettings):
    llm_provider: str
    llm_model: str
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_max_input_tokens: int = 6000
    llm_max_output_tokens: int = 512
    llm_timeout_seconds: float = 30.0
    llm_transport_retries: int = 2
    llm_malformed_output_retries: int = 1
    llm_cache_enabled: bool = True
```

Required environment examples:

| Provider | Required configuration |
|---|---|
| `openai` | `LLM_PROVIDER=openai`, `LLM_MODEL=<model-id>`, `LLM_API_KEY=<key>` |
| Kimi / Moonshot (OpenAI-compatible) | `LLM_PROVIDER=openai`, `LLM_MODEL=<kimi-model-id>`, `LLM_BASE_URL=https://api.moonshot.ai/v1`, `LLM_API_KEY=<key>` |
| `anthropic` | `LLM_PROVIDER=anthropic`, `LLM_MODEL=<model-id>`, `LLM_API_KEY=<key>` |

`LLM_PROVIDER=openai` selects the OpenAI-compatible wire protocol. Point `LLM_BASE_URL` at any
compatible endpoint (Kimi/Moonshot, Together, Azure OpenAI, local vLLM); leave it empty to use the
OpenAI default. `LLM_API_KEY` is the single API key for the configured provider.

## Provider Adapter Contract

```python
class LLMProvider(Protocol):
    name: str

    async def complete_structured(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        output_schema: dict[str, Any],
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> ProviderLLMResponse: ...
```

```python
class ProviderLLMResponse(BaseModel):
    content: dict[str, Any]
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    response_hash: str
    raw_status: str
```

## Gateway Interface

```python
class LLMGateway:
    def __init__(
        self,
        settings: LLMSettings,
        providers: Mapping[str, LLMProvider],
        cache: LLMCache | None = None,
    ) -> None: ...

    async def complete_structured(
        self,
        *,
        task: str,
        prompt_version: str,
        messages: list[dict[str, str]],
        output_schema: dict[str, Any],
        correlation_id: str,
        cache_key: str,
    ) -> LLMResult: ...
```

```python
class LLMResult(BaseModel):
    content: dict[str, Any]
    provider: str
    model: str
    prompt_version: str
    context_hash: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    attempt_count: int
    cache_hit: bool
    status: Literal["SUCCESS", "FAILED"]
    response_hash: str | None = None
```

## Structured Output Rules

- Prefer native structured output when a provider supports it.
- Use tool/schema forcing when native JSON mode is not available.
- Validate returned data against the caller-provided JSON schema or Pydantic model.
- Retry malformed structured output at most once.
- Never ask a second LLM call only to estimate tokens or cost.

## Token And Cost Controls

- Estimate compact input size before invocation when possible.
- Stop before invocation if configured input/output limits would be exceeded.
- Record provider-reported `input_tokens` and `output_tokens` from the response metadata.
- Store provider/model/prompt/context hashes for auditing.
- Cost calculation is metadata only and must use configured pricing tables or provider usage reports; it must not require another LLM call.

## Caching

Cache identity must include:

- provider
- model
- task
- prompt version
- schema hash
- context/input hash

Replaying the same completed request must return the cached response and make zero provider calls.

## Failure Handling

- Missing provider configuration fails fast at startup/readiness.
- Missing API key or cloud credentials fails readiness for services that require LLM access.
- Provider 429/5xx/transport errors use bounded retry.
- Malformed structured output uses at most one retry.
- Permanent failure returns a typed exception that the calling service converts into retryable or terminal domain state.

## Acceptance Criteria

1. `from shared.llm.gateway import LLMGateway` imports without error.
2. Provider selection is driven by `LLM_PROVIDER` and `LLM_MODEL`, not hardcoded service logic.
3. The API key is read from the single `LLM_API_KEY` variable, and `LLM_BASE_URL` (when set) targets any OpenAI-compatible endpoint.
4. Unit tests cover provider selection, missing-key failure, structured-output validation, malformed-output retry, and cache hit with zero provider call.
5. Integration tests are skipped unless the selected provider key/credentials are present.
6. Every result includes provider, model, prompt version, context hash, input tokens, output tokens, latency, attempt count, cache status, and response hash.
7. Re-running a cached request makes zero new provider calls.
8. Cleansing can inject the gateway without importing provider SDKs.
9. Prediction code has no M1 dependency on the gateway.
10. `mypy src/shared/llm/ --strict` and `ruff check src/shared/llm/` pass.

## Definition Of Done

- [x] `src/shared/llm/settings.py` defines provider/model/API-key configuration.
- [x] `src/shared/llm/providers.py` defines the adapter protocol and provider response model.
- [x] `src/shared/llm/gateway.py` implements the stable gateway interface.
- [x] At least one provider adapter is implemented for local integration testing.
- [x] Structured-output validation and malformed-output retry are covered by tests.
- [x] Provider usage metadata is persisted/logged without extra LLM calls.
- [ ] The Cleansing Service can use the gateway through dependency injection.
- [ ] Prediction-time usage remains disabled for M1.
