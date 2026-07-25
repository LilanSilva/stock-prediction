import pytest
import structlog

from shared.logging import setup_logging


@pytest.fixture(scope="session", autouse=True)
def _configure_logging() -> None:
    setup_logging("test", log_level="DEBUG")


@pytest.fixture(autouse=True)
def _clear_contextvars() -> None:
    # Isolate structlog contextvars per test so bindings from one test cannot leak into the next.
    structlog.contextvars.clear_contextvars()
