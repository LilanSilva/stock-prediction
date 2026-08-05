"""NotificationChannel Protocol — the only contract the engine depends on."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from notification.models import NotificationMessage


@runtime_checkable
class NotificationChannel(Protocol):
    """Any object satisfying this Protocol can be registered as a notification channel."""

    channel_id: str

    async def send(self, message: NotificationMessage) -> None:
        """Deliver the notification to all of this channel's recipients."""
        ...
