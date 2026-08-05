"""Recipient file loader — reads JSON arrays from disk once at startup."""

from __future__ import annotations

import json
import re
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

_E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")


def load_email_recipients(recipients_dir: str) -> list[str]:
    """Return email addresses from ``email_recipients.json``; empty list if absent."""
    path = Path(recipients_dir) / "email_recipients.json"
    return _load_strings(path, "email")


def load_whatsapp_recipients(recipients_dir: str) -> list[str]:
    """Return validated E.164 phone numbers from ``whatsapp_recipients.json``."""
    path = Path(recipients_dir) / "whatsapp_recipients.json"
    raw = _load_strings(path, "whatsapp")
    valid: list[str] = []
    for entry in raw:
        if _E164_RE.match(entry):
            valid.append(entry)
        else:
            logger.warning("whatsapp_recipient_invalid_e164", entry=entry)
    return valid


def _load_strings(path: Path, channel_id: str) -> list[str]:
    if not path.exists():
        logger.warning("recipient_file_absent", channel_id=channel_id, path=str(path))
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("recipient_file_parse_error", channel_id=channel_id, path=str(path), error=str(exc))
        return []
    if not isinstance(data, list) or not data:
        logger.warning("recipient_file_empty", channel_id=channel_id, path=str(path))
        return []
    return [str(item) for item in data]
