import unittest
from urllib.parse import unquote, urlparse

from flymail.application.uow import ApplicationUnitOfWork
from flymail.infrastructure.db.pool import redacted_database_url
from flymail.infrastructure.db.uow import SqlUnitOfWork
from tests.v2.mysql_test_case import MySqlIsolatedAsyncioTestCase


async def execute(connection, sql: str, params: tuple | list = ()) -> int:
    async with connection.cursor() as cursor:
        await cursor.execute(sql, params)
        return int(cursor.rowcount or 0)


class DatabaseUrlContractTests(unittest.TestCase):
    def test_database_url_redaction_removes_password_and_query(self):
        rendered = redacted_database_url(
            "mysql+aiomysql://mail%40user:p%40ss%2Fword@db.example:3307/flymail_v2?charset=utf8mb4&token=hidden"
        )

        self.assertEqual(rendered, "mysql://mail%40user:***@db.example:3307/flymail_v2")
        self.assertNotIn("word", rendered)
        self.assertNotIn("token", rendered)

    def test_redaction_rejects_non_mysql_or_missing_database_urls(self):
        for url in ("postgresql://user:pass@localhost/db", "mysql://user:pass@localhost"):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    redacted_database_url(url)


class SqlUnitOfWorkTests(MySqlIsolatedAsyncioTestCase):
    async def test_api_and_worker_pools_have_distinct_names_and_limits(self):
        self.assertEqual((self.api_pool.name, self.api_pool.minsize, self.api_pool.maxsize), ("flymail-api", 2, 12))
        self.assertEqual((self.worker_pool.name, self.worker_pool.minsize, self.worker_pool.maxsize), ("flymail-worker", 2, 8))

    async def test_pool_repr_uses_only_redacted_database_url(self):
        password = unquote(urlparse(self.database_url()).password or "")
        rendered = repr(self.api_pool)

        self.assertNotIn(self.database_url(), rendered)
        self.assertNotIn(password, rendered)
        self.assertIn(":***@", rendered)

    async def test_pool_uses_read_committed_and_disables_autocommit(self):
        async with self.api_pool.acquire() as connection:
            self.assertFalse(connection.get_autocommit())
            async with connection.cursor() as cursor:
                await cursor.execute("SELECT @@transaction_isolation")
                row = await cursor.fetchone()

        self.assertEqual(str(row[0]).upper(), "READ-COMMITTED")

    async def test_pool_rolls_back_open_transaction_before_connection_reuse(self):
        async with self.api_pool.acquire() as connection:
            await connection.begin()
            await execute(connection, "INSERT INTO v2_uow_probe(value_text) VALUES (%s)", ("discard-from-pool",))

        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM v2_uow_probe"), 0)

    async def test_uncommitted_insert_rolls_back_on_exit(self):
        async with SqlUnitOfWork(self.pool) as uow:
            await execute(uow.connection, "INSERT INTO v2_uow_probe(value_text) VALUES (%s)", ("discard",))

        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM v2_uow_probe"), 0)

    async def test_explicit_commit_persists(self):
        async with SqlUnitOfWork(self.pool) as uow:
            await execute(uow.connection, "INSERT INTO v2_uow_probe(value_text) VALUES (%s)", ("keep",))
            await uow.commit()

        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM v2_uow_probe"), 1)

    async def test_exception_rolls_back(self):
        with self.assertRaisesRegex(RuntimeError, "stop"):
            async with SqlUnitOfWork(self.pool) as uow:
                await execute(uow.connection, "INSERT INTO v2_uow_probe(value_text) VALUES (%s)", ("discard-error",))
                raise RuntimeError("stop")

        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM v2_uow_probe"), 0)

    async def test_commit_can_only_be_called_once(self):
        async with SqlUnitOfWork(self.pool) as uow:
            await uow.commit()
            with self.assertRaisesRegex(RuntimeError, "already completed"):
                await uow.commit()

    async def test_manual_rollback_prevents_later_commit(self):
        async with SqlUnitOfWork(self.pool) as uow:
            await execute(uow.connection, "INSERT INTO v2_uow_probe(value_text) VALUES (%s)", ("discard-manual",))
            await uow.rollback()
            with self.assertRaisesRegex(RuntimeError, "already completed"):
                await uow.commit()

        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM v2_uow_probe"), 0)

    async def test_sql_uow_satisfies_application_protocol(self):
        uow = SqlUnitOfWork(self.pool)
        self.assertIsInstance(uow, ApplicationUnitOfWork)

    async def test_pool_close_is_idempotent(self):
        pool = await type(self.api_pool).create(self.settings("api"))
        await pool.close()
        await pool.close()
        self.assertTrue(pool.closed)


if __name__ == "__main__":
    unittest.main()
