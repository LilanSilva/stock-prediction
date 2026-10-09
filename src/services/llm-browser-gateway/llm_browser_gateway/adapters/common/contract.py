"""Validate without rewriting the caller's body or allowing remote schema retrieval."""

import re
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from .json_codec import loads
from .types import GatewayError

ALLOWED = {
    "model",
    "messages",
    "response_format",
    "tools",
    "tool_choice",
    "parallel_tool_calls",
    "stream",
    "n",
    "max_tokens",
    "max_completion_tokens",
}
CAPABILITIES = frozenset({"text", "json", "tools"})
NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def require(condition: bool, message: str, param: str | None = None) -> None:
    if not condition:
        raise GatewayError(message, param=param)


def schema_check(schema: Any) -> None:
    require(isinstance(schema, dict), "Schema must be an object")
    count = 0

    def visit(value: Any, depth: int) -> None:
        nonlocal count
        count += 1
        require(depth <= 32 and count <= 5000, "Schema is too complex")
        if isinstance(value, dict):
            for key, child in value.items():
                require(
                    key not in {"$id", "$dynamicRef", "$recursiveRef"},
                    "Schema IDs and dynamic references are not supported",
                )
                if key == "$ref":
                    require(
                        isinstance(child, str) and (child == "#" or child.startswith("#/")),
                        "Only local JSON Pointer schema references are supported",
                    )
                    target: Any = schema
                    try:
                        for part in child[2:].split("/") if child != "#" else []:
                            part = part.replace("~1", "/").replace("~0", "~")
                            target = target[int(part)] if isinstance(target, list) else target[part]
                    except (KeyError, IndexError, ValueError, TypeError) as exc:
                        raise GatewayError(
                            "Unresolved local schema reference", param="schema"
                        ) from exc
                    require(isinstance(target, (dict, bool)), "Reference must resolve to a schema")
                visit(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                visit(child, depth + 1)

    visit(schema, 0)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise GatewayError("Invalid JSON Schema", param="schema") from exc


def text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    require(isinstance(content, list), "Only text message content is supported", "messages")
    pieces = []
    for block in content:
        require(
            isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str),
            "Only text blocks are supported",
            "messages",
        )
        pieces.append(block["text"])
    return "\n".join(pieces)


def validate(body: Any, alias: str) -> frozenset[str]:
    require(isinstance(body, dict), "Request body must be an object")
    unknown = set(body) - ALLOWED
    require(not unknown, f"Unsupported fields: {', '.join(sorted(unknown))}")
    require(body.get("model") == alias, f"Use the configured model alias {alias!r}", "model")
    require(body.get("stream", False) is False, "Streaming is not supported", "stream")
    require(type(body.get("n", 1)) is int and body.get("n", 1) == 1, "Only n=1 is supported", "n")
    for key in ("max_tokens", "max_completion_tokens"):
        if key in body and body[key] is not None:
            require(
                type(body[key]) is int and 0 < body[key] <= 32768,
                "Output budget must be an integer between 1 and 32768",
                key,
            )
    require(
        not (body.get("max_tokens") and body.get("max_completion_tokens")),
        "Specify only one output budget",
    )
    messages = body.get("messages")
    require(
        isinstance(messages, list) and 0 < len(messages) <= 256,
        "messages must contain 1 to 256 messages",
        "messages",
    )
    pending: set[str] = set()
    ids: set[str] = set()
    for message in messages:
        require(isinstance(message, dict), "Each message must be an object", "messages")
        role = message.get("role")
        require(
            role in {"system", "developer", "user", "assistant", "tool"},
            "Unsupported message role",
            "messages",
        )
        require(not pending or role == "tool", "Tool calls must be followed by their results")
        permitted = {"role", "content", "name"}
        if role == "assistant":
            permitted |= {"tool_calls", "refusal"}
        if role == "tool":
            permitted |= {"tool_call_id"}
        require(not set(message) - permitted, "Unsupported message fields", "messages")
        if message.get("content") is not None:
            text_content(message["content"])
        else:
            require(
                role == "assistant" and bool(message.get("tool_calls") or message.get("refusal")),
                "Message content is required",
                "messages",
            )
        if role == "tool":
            require(message.get("tool_call_id") in pending, "Unknown or duplicate tool_call_id")
            pending.remove(message["tool_call_id"])
        calls = message.get("tool_calls", [])
        require(isinstance(calls, list), "tool_calls must be an array")
        for call in calls:
            require(
                isinstance(call, dict) and call.get("type") == "function",
                "Only function tool calls are supported",
            )
            call_id = call.get("id")
            require(
                isinstance(call_id, str) and bool(call_id) and call_id not in ids,
                "Tool call IDs must be unique",
            )
            fn = call.get("function")
            require(
                isinstance(fn, dict)
                and isinstance(fn.get("name"), str)
                and isinstance(fn.get("arguments"), str),
                "Invalid function call",
            )
            try:
                arguments = loads(fn["arguments"])
            except (ValueError, TypeError) as exc:
                raise GatewayError("Function arguments must be JSON") from exc
            require(isinstance(arguments, dict), "Function arguments must be an object")
            ids.add(call_id)
            pending.add(call_id)
    require(not pending, "Missing tool results")
    required = {"text"}
    fmt = body.get("response_format") or {"type": "text"}
    require(isinstance(fmt, dict), "Invalid response_format", "response_format")
    require(
        fmt.get("type") in {"text", "json_object", "json_schema"}, "Unsupported response_format"
    )
    require(not set(fmt) - {"type", "json_schema"}, "Unsupported response_format fields")
    if fmt["type"] != "text":
        required.add("json")
    if fmt["type"] == "json_schema":
        spec = fmt.get("json_schema")
        if not isinstance(spec, dict):
            raise GatewayError("json_schema must be an object", param="response_format")
        require(
            isinstance(spec.get("name"), str) and bool(NAME.fullmatch(spec["name"])),
            "Invalid JSON Schema name",
        )
        require(
            not set(spec) - {"name", "schema", "description", "strict"},
            "Unsupported JSON Schema fields",
        )
        if "strict" in spec:
            require(type(spec["strict"]) is bool, "strict must be boolean")
        schema_check(spec.get("schema"))
    tools = body.get("tools") or []
    require(isinstance(tools, list) and len(tools) <= 64, "tools must be an array of at most 64")
    names: set[str] = set()
    for tool in tools:
        require(
            isinstance(tool, dict)
            and tool.get("type") == "function"
            and set(tool) <= {"type", "function"},
            "Only function tools are supported",
        )
        fn = tool.get("function")
        require(
            isinstance(fn, dict)
            and isinstance(fn.get("name"), str)
            and bool(NAME.fullmatch(fn["name"])),
            "Invalid tool function name",
        )
        require(
            not set(fn) - {"name", "description", "parameters", "strict"}, "Unsupported tool fields"
        )
        require(fn["name"] not in names, "Tool names must be unique")
        names.add(fn["name"])
        if "strict" in fn:
            require(type(fn["strict"]) is bool, "Tool strict must be boolean")
        schema_check(fn.get("parameters", {"type": "object"}))
    choice = body.get("tool_choice")
    if choice is not None:
        if isinstance(choice, str):
            require(choice in {"auto", "none", "required"}, "Unsupported tool_choice")
            require(choice != "required" or bool(tools), "required needs tools")
        else:
            require(
                isinstance(choice, dict)
                and choice.get("type") == "function"
                and isinstance(choice.get("function"), dict)
                and choice["function"].get("name") in names,
                "Unknown named tool",
            )
    if "parallel_tool_calls" in body:
        require(type(body["parallel_tool_calls"]) is bool, "parallel_tool_calls must be boolean")
    if tools:
        required.add("tools")
    return frozenset(required)
