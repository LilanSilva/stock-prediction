import uvicorn
from shared.logging import setup_logging

from .app import create_app
from .config import Settings


def main() -> None:
    settings = Settings()
    setup_logging("llm-browser-gateway")
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        workers=1,
        ws="wsproto",
        ws_max_size=settings.max_body_bytes * 2,
        access_log=False,
    )


if __name__ == "__main__":
    main()
