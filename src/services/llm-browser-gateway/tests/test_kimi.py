"""Real SDK/engine/adapter/bridge contract, with synthetic browser replies."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import MemoryStore
from fastapi.testclient import TestClient
from openai import OpenAI

from llm_browser_gateway.app import create_app
from llm_browser_gateway.config import Settings
from llm_browser_gateway.db import Availability


@pytest.mark.parametrize("mode", ["text", "json", "tool"])
def test_kimi_sdk_routing_and_shared_formatting(
    settings: Settings, store: MemoryStore, mode: str
) -> None:
    settings.priority = "chatgpt,claude,deepseek,meta,kimi"
    for provider in ("chatgpt", "claude", "deepseek", "meta"):
        store.states["default", provider] = Availability(
            "rate_limited", next_check_at=datetime.now(UTC) + timedelta(hours=1)
        )
    options: dict[str, Any] = {}
    envelope: dict[str, Any] = {"kind": "final", "content": "kimi-ready"}
    if mode == "json":
        options["response_format"] = {"type": "json_object"}
        envelope["content"] = {"ok": True}
    elif mode == "tool":
        options.update(
            tools=[{"type": "function", "function": {
                "name": "lookup", "parameters": {"type": "object", "properties": {
                    "item": {"type": "string"}}, "required": ["item"],
                    "additionalProperties": False}}}],
            tool_choice="required", parallel_tool_calls=False,
        )
        envelope = {"kind": "tool_calls", "calls": [
            {"name": "lookup", "arguments": {"item": "example"}}]}
    with TestClient(create_app(settings, store=store)) as http, ThreadPoolExecutor() as worker:
        with http.websocket_connect(
            "/bridge", headers={"origin": "chrome-extension://" + "a" * 32}
        ) as ws:
            ws.send_json({"type": "authenticate", "profileId": "default", "token": "b" * 32,
                          "protocolVersion": 2, "extensionVersion": "0.1.15"})
            assert ws.receive_json()["type"] == "authenticated"
            sdk = OpenAI(api_key="a" * 32, base_url="http://testserver/v1",
                         http_client=http, max_retries=0)
            pending = worker.submit(lambda: sdk.chat.completions.create(
                model="browser-auto", messages=[{"role": "user", "content": "test"}], **options
            ))
            probe = ws.receive_json()
            assert probe["type"] == "probe" and probe["provider"] == "kimi"
            ws.send_json({"type": "result", "attemptId": probe["attemptId"],
                          "provider": "kimi", "status": "success", "submitted": False})
            assert ws.receive_json()["type"] == "job_ack"
            execute = ws.receive_json()
            assert execute["type"] == "execute" and execute["provider"] == "kimi"
            assert "REQUEST:" in execute["prompt"]
            ws.send_json({"type": "progress", "attemptId": execute["attemptId"],
                          "provider": "kimi", "phase": "submitting"})
            assert ws.receive_json()["type"] == "progress_ack"
            ws.send_json({"type": "result", "attemptId": execute["attemptId"],
                          "provider": "kimi", "status": "success", "submitted": True,
                          "text": json.dumps(envelope)})
            assert ws.receive_json()["type"] == "job_ack"
            response = pending.result(timeout=5)
            assert response.model == "kimi-web"
            message = response.choices[0].message
            if mode == "tool":
                assert message.tool_calls and len(message.tool_calls) == 1
                call = message.tool_calls[0]
                assert call.type == "function" and call.function.name == "lookup"
                assert json.loads(call.function.arguments) == {"item": "example"}
            elif mode == "json":
                assert json.loads(message.content or "null") == {"ok": True}
            else:
                assert message.content == "kimi-ready"
            assert len(store.reservations) == 1 and store.reservations[0][1] == "kimi"
            assert not store.active
