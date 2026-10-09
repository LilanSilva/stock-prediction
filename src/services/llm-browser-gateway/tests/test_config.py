import pytest
from pydantic import ValidationError

from llm_browser_gateway.config import Settings


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.2"])
def test_host_deployment_rejects_non_loopback(settings: Settings, host: str) -> None:
    with pytest.raises(ValidationError, match="Host mode must bind to loopback"):
        Settings(_env_file=None, **(settings.model_dump() | {"host": host}))


def test_container_binding_requires_explicit_deployment(settings: Settings) -> None:
    configured = Settings(
        _env_file=None,
        **(settings.model_dump() | {"deployment": "container", "host": "0.0.0.0"}),
    )
    assert configured.host == "0.0.0.0"
    assert configured.api_key == settings.api_key
    assert configured.extension_id == settings.extension_id


def test_container_cannot_silently_bind_to_inaccessible_loopback(settings: Settings) -> None:
    with pytest.raises(ValidationError, match="Container mode requires"):
        Settings(_env_file=None, **(settings.model_dump() | {"deployment": "container"}))
