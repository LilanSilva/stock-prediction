"""Feed Analyzer shared library.

Canonical message schemas, the RabbitMQ client, the provider-configurable LLM gateway, and
structured logging. Every service imports from here so the frozen contracts have exactly one
executable implementation.
"""

__version__ = "0.1.0"
