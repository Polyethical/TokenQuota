"""Entry point: ``tokenquota-server`` or ``python -m tokenquota_server``."""

import logging
import os


def main() -> None:
    import uvicorn

    from .app import create_app
    from .config import settings_from_env

    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    app = create_app(settings_from_env())
    uvicorn.run(app, host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", "8080")))


if __name__ == "__main__":
    main()
