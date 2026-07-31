"""Isolated FlyMail V2 Worker development entrypoint."""

from __future__ import annotations

import sys

from flymail.config import FlyMailSettings
from flymail.domain.errors import ConfigurationError


def run() -> None:
    FlyMailSettings.from_env("worker")
    raise ConfigurationError("V2 worker heartbeat service is not implemented")


def main() -> int:
    try:
        run()
    except ConfigurationError as exc:
        print(f"FlyMail V2 worker configuration error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
