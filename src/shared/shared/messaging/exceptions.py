"""Messaging exception types."""


class RabbitMQConnectionError(Exception):
    """Raised when a connection to RabbitMQ cannot be established."""


class MessagePublishError(Exception):
    """Raised when a message fails to publish after retries."""


class MessageProcessingError(Exception):
    """Raised by a consumer callback to signal a retriable processing failure."""


class MessagePoisonError(Exception):
    """Raised by a consumer callback to signal a non-retriable failure (dead-letter immediately)."""
