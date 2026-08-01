"""Isolated FlyMail V2 API development entrypoint.

This module deliberately does not import the legacy application or routes.
"""

from __future__ import annotations

import os

import uvicorn

from flymail.api.app import create_app
from flymail.config import FlyMailSettings


__all__ = ["create_app"]


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
