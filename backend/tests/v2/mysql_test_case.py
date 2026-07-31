"""Real-MySQL test helpers for FlyMail V2 integration tests."""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from flymail.config import FlyMailSettings
from flymail.infrastructure.db.pool import DatabasePool


class MySqlIsolatedAsyncioTestCase(unittest.IsolatedAsyncioTestCase):
    """Create isolated API and Worker pools against a dedicated test database."""

    api_pool: DatabasePool
    worker_pool: DatabasePool
    pool: DatabasePool

    @classmethod
    def database_url(cls) -> str:
        url = os.environ.get("FLYMAIL_TEST_DATABASE_URL", "").strip()
        if not url:
            raise unittest.SkipTest("FLYMAIL_TEST_DATABASE_URL is required for MySQL integration tests")
        return url

    @classmethod
    def settings(cls, role: str) -> FlyMailSettings:
        data_dir = Path("/tmp/flymail-v2-test-data")
        if role == "api":
            pool_name, maximum = "flymail-api", 12
        elif role == "worker":
            pool_name, maximum = "flymail-worker", 8
        else:
            raise ValueError(f"unsupported test role: {role}")
        return FlyMailSettings(
            role=role,  # type: ignore[arg-type]
            database_url=cls.database_url(),
            data_dir=data_dir,
            object_dir=data_dir / "objects" / "sha256",
            object_tmp_dir=data_dir / "objects" / ".tmp",
            session_secret="test-session-secret-value",
            db_pool_name=pool_name,
            db_min_connections=2,
            db_max_connections=maximum,
        )

    async def asyncSetUp(self) -> None:
        self.api_pool = await DatabasePool.create(self.settings("api"))
        self.worker_pool = await DatabasePool.create(self.settings("worker"))
        self.pool = self.api_pool
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT COUNT(*)
                    FROM information_schema.tables
                    WHERE table_schema = DATABASE() AND table_name = 'v2_uow_probe'
                    """
                )
                exists = int((await cursor.fetchone())[0] or 0) > 0
                if not exists:
                    await cursor.execute(
                        """
                        CREATE TABLE v2_uow_probe (
                            id BIGINT PRIMARY KEY AUTO_INCREMENT,
                            value_text VARCHAR(191) NOT NULL
                        )
                        """
                    )
                else:
                    await cursor.execute("TRUNCATE TABLE v2_uow_probe")
                await connection.commit()

    async def asyncTearDown(self) -> None:
        await self.worker_pool.close()
        await self.api_pool.close()

    async def scalar(self, sql: str, params: tuple | list = ()):
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(sql, params)
                row = await cursor.fetchone()
                return row[0] if row else None
