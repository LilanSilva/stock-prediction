import os
import shutil
import socket
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn
from conftest import FakeAdapter, MemoryStore
from fastapi import FastAPI
from test_contract import BASE

from llm_browser_gateway.adapters.common.formatting import parse
from llm_browser_gateway.adapters.common.types import Status, failure


@pytest.fixture
def http_url(app: FastAPI) -> Iterator[str]:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            app, host="127.0.0.1", port=port, ws="none", access_log=False, log_level="error"
        )
    )
    worker = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    worker.start()
    until = time.monotonic() + 5
    while not server.started and worker.is_alive() and time.monotonic() < until:
        time.sleep(0.02)
    assert server.started
    try:
        yield f"http://127.0.0.1:{port}/v1"
    finally:
        server.should_exit = True
        worker.join(5)
        sock.close()
        assert not worker.is_alive()


def test_openai_over_actual_http(http_url: str) -> None:
    openai = pytest.importorskip("openai")
    with openai.OpenAI(api_key="a" * 32, base_url=http_url, max_retries=0) as client:
        assert client.chat.completions.create(**BASE).choices[0].message.content == "hello"


def test_http_failure_stays_with_one_provider_and_next_request_can_use_another(
    http_url: str, app: FastAPI, adapter: FakeAdapter, store: MemoryStore
) -> None:
    openai = pytest.importorskip("openai")
    adapter.result = failure(Status.RATE_LIMITED, submitted=True)
    second = app.state.engine.adapters["claude"]
    assert isinstance(second, FakeAdapter)
    with openai.OpenAI(api_key="a" * 32, base_url=http_url, max_retries=0) as client:
        with pytest.raises(openai.RateLimitError) as caught:
            client.chat.completions.create(**BASE)
        request_id = caught.value.response.headers["x-request-id"]
        assert store.reservations == [(request_id, "chatgpt", 0)]
        assert not second.probes and not second.seen

        reply = client.chat.completions.create(**BASE)
        assert reply.model == "claude-web"
        assert len(store.reservations) == 2
        assert store.reservations[1][0] != request_id
        assert store.reservations[1][1:] == ("claude", 0)
        assert len(adapter.seen) == 1 and len(second.seen) == 1


def test_javascript_sdk_over_actual_http(http_url: str) -> None:
    node = shutil.which("node")
    root = Path(__file__).resolve().parents[1] / "extension"
    if not node or not (root / "node_modules" / "openai").exists():
        pytest.skip("Build extension dependencies to run the JavaScript SDK smoke test")
    result = subprocess.run(
        [node, str(root / "scripts" / "sdk-smoke.mjs")],
        env={**os.environ, "OPENAI_BASE_URL": http_url, "BROWSER_GATEWAY_API_KEY": "a" * 32},
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    assert result.stdout.strip() == "hello"


def test_langchain_when_installed(http_url: str, adapter: FakeAdapter) -> None:
    sdk = pytest.importorskip("langchain_openai")
    client = sdk.ChatOpenAI(
        api_key="a" * 32,
        base_url=http_url,
        model="browser-auto",
        max_retries=0,
        use_responses_api=False,
    )
    assert client.invoke("hello").content == "hello"
    schema = {
        "title": "Classification",
        "type": "object",
        "properties": {"event_type": {"enum": ["EARNINGS", "OTHER"]}},
        "required": ["event_type"],
        "additionalProperties": False,
    }
    adapter.result = parse(
        '{"kind":"final","content":"{\\"event_type\\":\\"EARNINGS\\"}"}', BASE, "chatgpt"
    )
    assert client.with_structured_output(schema, method="json_schema").invoke("classify") == {
        "event_type": "EARNINGS"
    }
    tool = {"type": "function", "function": {"name": "Classification", "parameters": schema}}
    adapter.result = parse(
        '{"kind":"tool_calls","calls":[{"name":"Classification",'
        '"arguments":{"event_type":"EARNINGS"}}]}',
        BASE | {"tools": [tool], "tool_choice": "required"},
        "chatgpt",
    )
    assert client.with_structured_output(schema, method="function_calling").invoke("classify") == {
        "event_type": "EARNINGS"
    }
    assert adapter.seen[-1]["tool_choice"]["function"]["name"] == "Classification"
    messages = pytest.importorskip("langchain_core.messages")
    raw = client.bind_tools([schema], tool_choice="Classification").invoke("classify")
    call_id = raw.tool_calls[0]["id"]
    adapter.result = parse('{"kind":"final","content":"received result"}', BASE, "chatgpt")
    reply = client.invoke(
        [
            messages.HumanMessage(content="classify"),
            raw,
            messages.ToolMessage(content='{"accepted":true}', tool_call_id=call_id),
        ]
    )
    assert reply.content == "received result"
    assert adapter.seen[-1]["messages"][-1]["tool_call_id"] == call_id
