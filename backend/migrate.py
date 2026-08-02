"""Run FlyMail V2 database migrations as a one-shot container command."""

from __future__ import annotations

import asyncio
import os

from flymail.config import FlyMailSettings
from flymail.domain.errors import ConfigurationError
from flymail.infrastructure.db.migrations.runner import run_migrations
from flymail.infrastructure.db.pool import DatabasePool
from flymail.infrastructure.security.passwords import hash_password
from flymail.repositories.base import AdminContext
from flymail.repositories.users import UserRepository


_ADMIN_BOOTSTRAP_LOCK = "flymail_v2_initial_admin"


async def bootstrap_initial_admin(
    pool: DatabasePool,
    *,
    username: str,
    password: str,
) -> bool:
    """Create exactly one initial administrator when the database has no users."""

    normalized_username = str(username or "").strip()
    normalized_password = str(password or "")
    async with pool.acquire() as connection:
        async with connection.cursor() as cursor:
            await cursor.execute("SELECT GET_LOCK(%s, 30)", (_ADMIN_BOOTSTRAP_LOCK,))
            lock_row = await cursor.fetchone()
        if not lock_row or int(lock_row[0] or 0) != 1:
            raise RuntimeError("could not acquire initial administrator bootstrap lock")
        try:
            await connection.begin()
            async with connection.cursor() as cursor:
                await cursor.execute("SELECT id FROM users ORDER BY id LIMIT 1 FOR UPDATE")
                existing = await cursor.fetchone()
            if existing is not None:
                await connection.rollback()
                return False
            if not normalized_username:
                raise ConfigurationError(
                    "FLYMAIL_ADMIN_USERNAME is required for an empty database"
                )
            if len(normalized_username) > 191:
                raise ConfigurationError(
                    "FLYMAIL_ADMIN_USERNAME must not exceed 191 characters"
                )
            if len(normalized_password) < 12:
                raise ConfigurationError(
                    "FLYMAIL_ADMIN_PASSWORD must be at least 12 characters for an empty database"
                )
            await UserRepository(connection).create_user_for_admin(
                AdminContext("usr_initial_bootstrap"),
                username=normalized_username,
                password_hash=await asyncio.to_thread(
                    hash_password,
                    normalized_password,
                ),
                role="admin",
                enabled=True,
            )
            await connection.commit()
            return True
        except Exception:
            await connection.rollback()
            raise
        finally:
            async with connection.cursor() as cursor:
                await cursor.execute("SELECT RELEASE_LOCK(%s)", (_ADMIN_BOOTSTRAP_LOCK,))
                await cursor.fetchone()


async def migrate() -> None:
    settings = FlyMailSettings.from_env("worker")
    pool = await DatabasePool.create(settings)
    try:
        await run_migrations(pool)
        await bootstrap_initial_admin(
            pool,
            username=os.environ.get("FLYMAIL_ADMIN_USERNAME", ""),
            password=os.environ.get("FLYMAIL_ADMIN_PASSWORD", ""),
        )
    finally:
        await pool.close()


def main() -> int:
    asyncio.run(migrate())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
