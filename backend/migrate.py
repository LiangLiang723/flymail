"""Run FlyMail V2 database migrations as a one-shot container command."""

from __future__ import annotations

import asyncio

from flymail.config import FlyMailSettings
from flymail.infrastructure.db.migrations.runner import run_migrations
from flymail.infrastructure.db.pool import DatabasePool


async def migrate() -> None:
    settings = FlyMailSettings.from_env("worker")
    pool = await DatabasePool.create(settings)
    try:
        await run_migrations(pool)
    finally:
        await pool.close()


def main() -> int:
    asyncio.run(migrate())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
