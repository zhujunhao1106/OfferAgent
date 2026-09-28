"""Entrypoint: load env, assemble config + services, run uvicorn (mirrors cmd/offerpilot-api/main.go)."""
from __future__ import annotations

import asyncio
import logging

import uvicorn

from . import config
from .api import create_app
from .settings import Settings, from_env
from .wiring import assemble

_LOG_LEVELS = {
    "debug": logging.DEBUG,
    "warn": logging.WARNING,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "info": logging.INFO,
}


def main() -> None:
    config.load_env()
    settings = from_env()
    logging.basicConfig(
        level=_LOG_LEVELS.get(config.env_or("LOG_LEVEL", "info").lower(), logging.INFO),
        format="%(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run(settings))


async def run(settings: Settings) -> None:
    app_config = await assemble(settings)
    server = uvicorn.Server(uvicorn.Config(
        create_app(app_config),
        host="0.0.0.0",
        port=config.int_env("PORT", 3001),
        workers=1,
        log_level="info",
    ))
    try:
        await server.serve()
    finally:
        store = getattr(app_config, "store", None)
        if store is not None:
            await store.close()


if __name__ == "__main__":
    main()
