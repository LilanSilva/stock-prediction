import copy
import json

import pytest

from llm_browser_gateway.adapters.common.contract import validate
from llm_browser_gateway.adapters.common.formatting import parse, render
from llm_browser_gateway.adapters.common.types import JSON, GatewayError, Status

BASE: JSON = {"model": "browser-auto", "messages": [{"role": "user", "content": "hello"}]}
TOOL: JSON = {
    "type": "function",
    "function": {
        "name": "lookup",
        "parameters": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
            "additionalProperties": False,
        },
    },
}
SCHEMA = {
    "type": "object",
    "properties": {"event_type": {"enum": ["OTHER", "EARNINGS"]}},
    "required": ["event_type"],
    "additionalProperties": False,
}


def test_contract_preserves_body() -> None:
    body = copy.deepcopy(BASE)
    body["messages"] = [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": [{"type": "text", "text": "article"}]},
    ]
    before = copy.deepcopy(body)
    assert validate(body, "browser-auto") == {"text"}
    assert body == before
    assert json.loads(render(body).split("REQUEST:\n")[1]) == before


@pytest.mark.parametrize(
    "extra",
    [
        {"stream": True},
        {"temperature": 0.2},
        {"n": 2},
        {"n": True},
        {"tools": [dict(TOOL, function=TOOL["function"] | {"strict": "yes"})]},
        {"max_tokens": -1},
        {"max_tokens": 20, "max_completion_tokens": 20},
        {"messages": []},
        {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": "x"}]}]},
        {"tools": [TOOL], "tool_choice": {"type": "function", "function": {"name": "unknown"}}},
        {
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "data", "schema": {"$ref": "https://example.com/schema"}},
            }
        },
    ],
)
def test_unsupported_input(extra: JSON) -> None:
    with pytest.raises(GatewayError):
        validate(BASE | extra, "browser-auto")


def test_json_schema_output_enforced() -> None:
    body = BASE | {
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "classification", "schema": SCHEMA, "strict": True},
        }
    }
    assert validate(body, "browser-auto") == {"text", "json"}
    good = parse('{"kind":"final","content":{"event_type":"OTHER"}}', body, "claude")
    assert json.loads(good.body["choices"][0]["message"]["content"]) == {"event_type": "OTHER"}
    assert good.body["model"] == "claude-web"
    assert good.body["usage"] is None
    for content in [{"event_type": "WRONG"}, {"event_type": "OTHER", "extra": 1}, {}]:
        with pytest.raises(GatewayError):
            parse(json.dumps({"kind": "final", "content": content}), body, "claude")


def test_function_round_trip() -> None:
    body = BASE | {"tools": [TOOL], "tool_choice": "required", "parallel_tool_calls": False}
    validate(body, "browser-auto")
    response = parse(
        '{"kind":"tool_calls","calls":[{"name":"lookup","arguments":{"symbol":"AAPL"}}]}',
        body,
        "chatgpt",
    )
    choice = response.body["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    call = choice["message"]["tool_calls"][0]
    assert json.loads(call["function"]["arguments"]) == {"symbol": "AAPL"}
    followup = BASE | {
        "tools": [TOOL],
        "messages": [
            *BASE["messages"],
            choice["message"],
            {"role": "tool", "tool_call_id": call["id"], "content": "price=10"},
        ],
    }
    validate(followup, "browser-auto")
    assert parse('{"kind":"final","content":"price is 10"}', followup, "claude").http_status == 200
    with pytest.raises(GatewayError):
        validate(BASE | {"messages": [*BASE["messages"], choice["message"]]}, "browser-auto")


@pytest.mark.parametrize(
    "output",
    [
        '{"kind":"tool_calls","calls":[{"name":"unknown","arguments":{}}]}',
        '{"kind":"tool_calls","calls":[{"name":"lookup","arguments":{"symbol":123}}]}',
        '{"kind":"final","content":"I will call lookup"}',
    ],
)
def test_invalid_tool_output(output: str) -> None:
    with pytest.raises(GatewayError):
        parse(output, BASE | {"tools": [TOOL], "tool_choice": "required"}, "chatgpt")


def test_refusal_is_terminal() -> None:
    response = parse('{"kind":"refusal","content":"Cannot answer"}', BASE, "claude")
    assert response.status == Status.REFUSED
    assert response.body["choices"][0]["message"]["refusal"] == "Cannot answer"


def test_code_fence_preserves_json_quotes() -> None:
    reply = parse('```json\n{"kind":"final","content":"hello"}\n```', BASE, "claude")
    assert reply.body["choices"][0]["message"]["content"] == "hello"


def test_local_schema_references() -> None:
    schema = {"$defs": {"answer": SCHEMA}, "$ref": "#/$defs/answer"}
    fmt = {"type": "json_schema", "json_schema": {"name": "data", "schema": schema}}
    body = BASE | {"response_format": fmt}
    validate(body, "browser-auto")
    parse('{"kind":"final","content":{"event_type":"OTHER"}}', body, "chatgpt")
    for bad in [{"$ref": "#/missing"}, {"$id": "https://example.com"}, {"$ref": "#anchor"}]:
        with pytest.raises(GatewayError):
            validate(
                BASE
                | {
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {"name": "data", "schema": bad},
                    }
                },
                "browser-auto",
            )


def test_non_json_constants_rejected() -> None:
    with pytest.raises(GatewayError):
        parse(
            '{"kind":"final","content":{"x":NaN}}',
            BASE | {"response_format": {"type": "json_object"}},
            "chatgpt",
        )


def test_nested_json_text_preserves_escapes_and_rejects_unescaped_quotes() -> None:
    nested = '{"status":"done"}'
    response = parse(json.dumps({"kind": "final", "content": nested}), BASE, "kimi")
    assert response.body["choices"][0]["message"]["content"] == nested
    with pytest.raises(GatewayError):
        parse('{"kind":"final","content":"{"status":"done"}"}', BASE, "kimi")
