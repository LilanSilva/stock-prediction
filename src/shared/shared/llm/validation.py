"""Standards-compliant JSON Schema validation for structured LLM output."""

from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError


def validate_schema(schema: dict[str, Any]) -> None:
    """Validate the caller's schema before a provider call is attempted."""
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ValueError(f"invalid output schema: {exc.message}") from exc


def validate_against_schema(data: Any, schema: dict[str, Any]) -> None:
    """Validate data against Draft 2020-12 JSON Schema. Raise ValueError on mismatch."""
    validate_schema(schema)
    try:
        Draft202012Validator(schema).validate(data)
    except ValidationError as exc:
        location = ".".join(str(part) for part in exc.absolute_path) or "$"
        raise ValueError(f"{location}: {exc.message}") from exc
