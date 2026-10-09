"""Use actual JSON, rejecting Python's non-standard NaN/Infinity extensions."""

import json
from typing import Any


def reject_constant(value: str) -> Any:
    raise ValueError(f"Invalid JSON constant: {value}")


def loads(value: str | bytes | bytearray) -> Any:
    return json.loads(value, parse_constant=reject_constant)
