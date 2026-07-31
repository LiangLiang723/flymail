"""Isolated FlyMail V2 API development entrypoint.

This module deliberately does not import the legacy application or routes.
"""

from __future__ import annotations

import os

import uvicorn
from fastapi import FastAPI

from flymail.config import FlyMailSettings
from version import VERSION


def create_app(settings: FlyMailSettings | None = None) -> FastAPI:
    runtime_settings = settings or FlyMailSettings.from_env("api")
    app = FastAPI(
        title="FlyMail V2",
        description="FlyMail V2 isolated development API",
        version=VERSION,
    )

    @app.get("/api/v2/health")
    async def health() -> dict[str, str]:
        return {
            "status": "ok",
            "app": "flymail-v2",
            "role": runtime_settings.role,
            "version": VERSION,
        }

    return app


def main() -> None:
    settings = FlyMailSettings.from_env("api")
    uvicorn.run(
        create_app(settings),
        host=os.environ.get("APP_HOST", "127.0.0.1"),
        port=int(os.environ.get("APP_PORT", "8081")),
        log_level="warning",
    )


if __name__ == "__main__":
    main()
