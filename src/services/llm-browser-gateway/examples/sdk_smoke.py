"""Opt-in live browser checks; sends only synthetic messages to the paired websites."""

import argparse
import asyncio
import json
import time

import httpx
from openai import AsyncOpenAI, OpenAI

from llm_browser_gateway.config import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--provider", choices=["chatgpt", "claude", "deepseek", "meta", "kimi", "gemini"]
    )
    parser.add_argument("--text-only", action="store_true")
    args = parser.parse_args()
    settings = Settings()
    if args.provider:
        status = httpx.get(
            f"http://127.0.0.1:{settings.port}/status",
            headers={"Authorization": f"Bearer {settings.api_key.get_secret_value()}"},
        )
        status.raise_for_status()
        assert status.json()["priority"] == [args.provider], (
            "Start the gateway with BROWSER_GATEWAY_PRIORITY=" + args.provider
        )
        assert status.json()["extension_connected"], "Pair the Chrome extension first"

    def verify_model(model: str) -> None:
        if args.provider:
            assert model == f"{args.provider}-web", f"Unexpected provider: {model}"

    started = time.monotonic()
    options = {
        "api_key": settings.api_key.get_secret_value(),
        "base_url": f"http://127.0.0.1:{settings.port}/v1",
        "timeout": settings.deadline_seconds + 30,
        "max_retries": 0,
    }
    with OpenAI(**options) as client:  # type: ignore[arg-type]
        text = client.chat.completions.create(
            model=settings.model_alias,
            messages=[{"role": "user", "content": "Reply with exactly: gateway works"}],
        )
        verify_model(text.model)
        assert text.choices[0].message.content == "gateway works"
        print("Text: validated", text.model, flush=True)
        if args.text_only:
            return
        structured = client.chat.completions.create(
            model=settings.model_alias,
            messages=[{"role": "user", "content": "Return an object with ok=true."}],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "check",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {"ok": {"const": True}},
                        "required": ["ok"],
                        "additionalProperties": False,
                    },
                },
            },
        )
        verify_model(structured.model)
        assert json.loads(structured.choices[0].message.content or "null") == {"ok": True}
        print("JSON: validated")
        messages = [
            {"role": "user", "content": "Use lookup_demo for item=example, then report it."}
        ]
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "lookup_demo",
                    "description": "Look up a synthetic demonstration item.",
                    "parameters": {
                        "type": "object",
                        "properties": {"item": {"type": "string"}},
                        "required": ["item"],
                        "additionalProperties": False,
                    },
                    "strict": True,
                },
            }
        ]
        tool_response = client.chat.completions.create(
            model=settings.model_alias,
            messages=messages,
            tools=tools,  # type: ignore[arg-type]
            tool_choice="required",
            parallel_tool_calls=False,
        )
        verify_model(tool_response.model)
        message = tool_response.choices[0].message
        assert message.tool_calls and len(message.tool_calls) == 1
        call = message.tool_calls[0]
        assert call.type == "function" and call.function.name == "lookup_demo"
        assert json.loads(call.function.arguments) == {"item": "example"}
        print("Tool call: validated", flush=True)
        followup = [
            *messages,
            message.model_dump(exclude_none=True),
            {"role": "tool", "tool_call_id": call.id, "content": '{"value":42}'},
        ]
        final = client.chat.completions.create(
            model=settings.model_alias,
            messages=followup,
            tools=tools,  # type: ignore[arg-type]
            tool_choice="none",
        )
        verify_model(final.model)
        assert "42" in (final.choices[0].message.content or "")
        print("Tools: validated round trip")

    async def check_async() -> None:
        async with AsyncOpenAI(**options) as client:  # type: ignore[arg-type]
            response = await client.chat.completions.create(
                model=settings.model_alias,
                messages=[{"role": "user", "content": "Reply with exactly: async works"}],
            )
            verify_model(response.model)
            assert response.choices[0].message.content == "async works"
            print("Async: validated", response.model)

    asyncio.run(check_async())
    print(f"All five live requests passed in {time.monotonic() - started:.1f}s")


if __name__ == "__main__":
    main()
