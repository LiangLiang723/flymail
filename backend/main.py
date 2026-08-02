"""FlyMail V2 formal API entrypoint."""

from __future__ import annotations

from flymail.api.app import create_app
from flymail.config import FlyMailSettings


settings = FlyMailSettings.from_env("api")
app = create_app(settings)


__all__ = ["app", "settings"]
