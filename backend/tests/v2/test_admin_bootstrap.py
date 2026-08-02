"""One-time first-administrator bootstrap for fresh FlyMail V2 databases."""

from __future__ import annotations

from flymail.domain.errors import ConfigurationError
from flymail.infrastructure.db.migrations.runner import run_migrations
from flymail.infrastructure.security.passwords import verify_password
from flymail.repositories.base import AdminContext
from flymail.repositories.users import UserRepository
from tests.v2.mysql_test_case import MySqlIsolatedAsyncioTestCase

from migrate import bootstrap_initial_admin


class InitialAdminBootstrapTests(MySqlIsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await run_migrations(self.api_pool)
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                for table in (
                    "user_settings",
                    "user_profiles",
                    "users",
                ):
                    await cursor.execute(f"DELETE FROM {table}")
            await connection.commit()

    async def test_fresh_database_requires_and_creates_one_admin(self):
        created = await bootstrap_initial_admin(
            self.api_pool,
            username="initial-admin",
            password="InitialAdminPassword!123",
        )
        self.assertTrue(created)

        async with self.api_pool.acquire() as connection:
            record = await UserRepository(connection).find_for_authentication("initial-admin")
            async with connection.cursor() as cursor:
                await cursor.execute("SELECT COUNT(*) FROM user_profiles")
                profiles = int((await cursor.fetchone())[0] or 0)
                await cursor.execute("SELECT COUNT(*) FROM user_settings")
                settings = int((await cursor.fetchone())[0] or 0)
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.user.role, "admin")
        self.assertTrue(record.user.enabled)
        self.assertTrue(verify_password("InitialAdminPassword!123", record.password_hash))
        self.assertEqual(profiles, 1)
        self.assertEqual(settings, 1)

    async def test_existing_users_are_never_reset_by_bootstrap_values(self):
        await bootstrap_initial_admin(
            self.api_pool,
            username="existing-admin",
            password="OriginalAdminPassword!123",
        )
        async with self.api_pool.acquire() as connection:
            before = await UserRepository(connection).find_for_authentication("existing-admin")
        assert before is not None

        created = await bootstrap_initial_admin(
            self.api_pool,
            username="replacement-admin",
            password="ReplacementPassword!123",
        )
        self.assertFalse(created)

        async with self.api_pool.acquire() as connection:
            after = await UserRepository(connection).find_for_authentication("existing-admin")
            replacement = await UserRepository(connection).find_for_authentication("replacement-admin")
            users = await UserRepository(connection).list_users_for_admin(
                AdminContext("usr_bootstrap_test")
            )
        assert after is not None
        self.assertEqual(before.password_hash, after.password_hash)
        self.assertTrue(verify_password("OriginalAdminPassword!123", after.password_hash))
        self.assertIsNone(replacement)
        self.assertEqual(len(users), 1)

    async def test_empty_database_rejects_missing_or_weak_bootstrap_values(self):
        with self.assertRaises(ConfigurationError):
            await bootstrap_initial_admin(self.api_pool, username="", password="")
        with self.assertRaises(ConfigurationError):
            await bootstrap_initial_admin(
                self.api_pool,
                username="initial-admin",
                password="short",
            )

    async def test_existing_database_allows_bootstrap_values_to_be_absent(self):
        await bootstrap_initial_admin(
            self.api_pool,
            username="existing-admin",
            password="OriginalAdminPassword!123",
        )
        created = await bootstrap_initial_admin(
            self.api_pool,
            username="",
            password="",
        )
        self.assertFalse(created)
