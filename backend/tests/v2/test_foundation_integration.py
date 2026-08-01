from __future__ import annotations

import os
import re
import tempfile
import unittest
from pathlib import Path
from urllib.parse import unquote, urlparse

import aiomysql
import httpx

from flymail.config import FlyMailSettings
from flymail.domain.enums import ObjectKind
from flymail.infrastructure.db.migrations.runner import current_schema_version, run_migrations
from flymail.infrastructure.db.pool import DatabasePool
from flymail.infrastructure.db.uow import SqlUnitOfWork
from flymail.infrastructure.object_store.store import ObjectStore
from flymail.infrastructure.security.credentials import CredentialCipher
from flymail.infrastructure.security.passwords import hash_password
from flymail.repositories.accounts import AccountRepository, CredentialRepository
from flymail.repositories.base import AdminContext, TenantContext
from flymail.repositories.jobs import JobRepository, JobSpec
from flymail.repositories.objects import ObjectRepository
from flymail.repositories.outbox import OutboxRepository
from flymail.repositories.users import UserRepository
from v2_dev import create_app


async def chunks(*values: bytes):
    for value in values:
        yield value


class FoundationIntegrationTests(unittest.IsolatedAsyncioTestCase):
    api_pool: DatabasePool | None
    worker_pool: DatabasePool | None

    @classmethod
    def database_url(cls) -> str:
        value = os.environ.get("FLYMAIL_TEST_DATABASE_URL", "").strip()
        if not value:
            raise unittest.SkipTest("FLYMAIL_TEST_DATABASE_URL is required for foundation integration")
        return value

    @classmethod
    def database_name(cls) -> str:
        name = unquote(urlparse(cls.database_url()).path.lstrip("/"))
        if not re.fullmatch(r"[A-Za-z0-9_]+", name):
            raise RuntimeError("foundation test database name must be alphanumeric with underscores")
        return name

    @classmethod
    def database_user(cls) -> str:
        user = unquote(urlparse(cls.database_url()).username or "")
        if not re.fullmatch(r"[A-Za-z0-9_]+", user):
            raise RuntimeError("foundation test database user must be alphanumeric with underscores")
        return user

    async def asyncSetUp(self) -> None:
        socket_path = os.environ.get("FLYMAIL_TEST_MYSQL_SOCKET", "/run/mysqld/mysqld.sock")
        if not os.path.exists(socket_path):
            raise unittest.SkipTest("root MySQL socket is required for foundation integration")

        database = self.database_name()
        database_user = self.database_user()
        admin = await aiomysql.connect(
            user="root",
            unix_socket=socket_path,
            db="mysql",
            charset="utf8mb4",
            autocommit=True,
        )
        try:
            async with admin.cursor() as cursor:
                await cursor.execute(
                    "SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name = %s",
                    (database,),
                )
                if int((await cursor.fetchone())[0] or 0) > 0:
                    await cursor.execute(f"DROP DATABASE `{database}`")
                await cursor.execute(
                    f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
                )
                await cursor.execute(
                    f"GRANT ALL PRIVILEGES ON `{database}`.* TO '{database_user}'@'127.0.0.1'"
                )
        finally:
            admin.close()

        self.temp_dir = tempfile.TemporaryDirectory(prefix="flymail-v2-foundation-")
        self.data_dir = Path(self.temp_dir.name) / "data"
        self.data_dir.mkdir(parents=True)
        self.api_pool = None
        self.worker_pool = None

    async def asyncTearDown(self) -> None:
        if self.worker_pool is not None:
            await self.worker_pool.close()
        if self.api_pool is not None:
            await self.api_pool.close()
        self.temp_dir.cleanup()

    def settings(self, role: str) -> FlyMailSettings:
        if role == "api":
            pool_name, maximum = "flymail-api", 12
        elif role == "worker":
            pool_name, maximum = "flymail-worker", 8
        else:
            raise ValueError("unsupported role")
        return FlyMailSettings(
            role=role,  # type: ignore[arg-type]
            database_url=self.database_url(),
            data_dir=self.data_dir,
            object_dir=self.data_dir / "objects" / "sha256",
            object_tmp_dir=self.data_dir / "objects" / ".tmp",
            session_secret="foundation-integration-session-secret",
            db_pool_name=pool_name,
            db_min_connections=2,
            db_max_connections=maximum,
        )

    async def open_pools(self) -> None:
        self.api_pool = await DatabasePool.create(self.settings("api"))
        self.worker_pool = await DatabasePool.create(self.settings("worker"))

    async def close_pools(self) -> None:
        if self.worker_pool is not None:
            await self.worker_pool.close()
            self.worker_pool = None
        if self.api_pool is not None:
            await self.api_pool.close()
            self.api_pool = None

    async def test_empty_database_to_restart_and_last_reference_cleanup(self):
        await self.open_pools()
        assert self.api_pool is not None
        assert self.worker_pool is not None

        self.assertEqual(await run_migrations(self.api_pool), [1, 2, 3, 4, 5, 6, 7, 8])
        store = ObjectStore(
            self.settings("api").object_dir,
            self.settings("api").object_tmp_dir,
        )
        stored = await store.put_stream(
            ObjectKind.BODY_TEXT,
            chunks(b"foundation ", b"body"),
            expected_size=len(b"foundation body"),
        )
        cipher = CredentialCipher.from_master_secret(
            "foundation-independent-credential-secret"
        )

        async with SqlUnitOfWork(self.api_pool) as uow:
            assert uow.connection is not None
            users = UserRepository(uow.connection)
            user = await users.create_user_for_admin(
                AdminContext("usr_foundation_admin"),
                username="foundation-user",
                password_hash=hash_password("foundation-password"),
            )
            tenant = TenantContext(user.id)
            account = await AccountRepository(uow.connection).create_account(
                tenant,
                provider_key="custom_imap",
                email="foundation@example.com",
                status="active",
            )
            encrypted = cipher.encrypt(account.id, b"foundation-mail-secret")
            await CredentialRepository(uow.connection).store_encrypted(
                tenant,
                account.id,
                credential_type="password",
                value=encrypted,
            )
            objects = ObjectRepository(uow.connection)
            async with objects.lock_object(stored.content_sha256):
                await objects.attach_reference(
                    stored,
                    user_uid=user.id,
                    reference_kind="message_body_text",
                    reference_id="msg_foundation",
                )
            job_id = await JobRepository(uow.connection).enqueue(
                JobSpec(
                    queue_name="foundation",
                    job_kind="foundation.verify",
                    user_uid=user.id,
                    dedupe_key="foundation-job",
                    payload={"account_id": account.id},
                )
            )
            event_id = await OutboxRepository(
                uow.connection,
                tenant,
                trace_id="trc_foundation",
            ).append(
                "foundation.created",
                account.id,
                {"account_id": account.id, "job_id": job_id},
            )
            await uow.commit()

        async with self.worker_pool.acquire() as connection:
            await connection.begin()
            claimed = await JobRepository(connection).claim(
                "foundation",
                "worker-foundation",
                limit=1,
                lease_seconds=60,
            )
            await connection.commit()
        self.assertEqual([item.id for item in claimed], [job_id])

        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("SELECT 1")
                self.assertEqual((await cursor.fetchone())[0], 1)

        transport = httpx.ASGITransport(app=create_app(self.settings("api")))
        async with httpx.AsyncClient(transport=transport, base_url="http://foundation") as client:
            response = await client.get("/api/v2/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "status": "ok",
                "role": "api",
                "schema_version": 8,
                "database": "ok",
                "object_store": "ok",
            },
        )

        user_uid = user.id
        account_id = account.id
        digest = stored.content_sha256
        self.assertTrue(stored.path.is_file())
        await self.close_pools()
        await self.open_pools()
        assert self.api_pool is not None
        assert self.worker_pool is not None
        self.assertEqual(await run_migrations(self.api_pool), [])

        tenant = TenantContext(user_uid)
        async with self.api_pool.acquire() as connection:
            users = UserRepository(connection)
            accounts = AccountRepository(connection)
            credentials = CredentialRepository(connection)
            objects = ObjectRepository(connection)
            loaded_user = await users.get_user(tenant)
            loaded_account = await accounts.get_account(tenant, account_id)
            loaded_credential = await credentials.get_encrypted(tenant, account_id)
            self.assertIsNotNone(loaded_user)
            self.assertIsNotNone(loaded_account)
            self.assertIsNotNone(loaded_credential)
            self.assertEqual(
                cipher.decrypt(account_id, loaded_credential.value),
                b"foundation-mail-secret",
            )
            self.assertEqual(await objects.count_references(digest), 1)
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "SELECT status, lease_owner FROM worker_jobs WHERE id = %s",
                    (job_id,),
                )
                self.assertEqual(await cursor.fetchone(), ("leased", "worker-foundation"))
                await cursor.execute(
                    "SELECT COUNT(*) FROM outbox_events WHERE id = %s",
                    (event_id,),
                )
                self.assertEqual((await cursor.fetchone())[0], 1)
                self.assertEqual(await current_schema_version(connection), 8)

        async with self.api_pool.acquire() as connection:
            objects = ObjectRepository(connection)
            async with objects.lock_object(digest):
                await connection.begin()
                detached = await objects.detach_reference(
                    user_uid=user_uid,
                    reference_kind="message_body_text",
                    reference_id="msg_foundation",
                )
                await connection.commit()
            self.assertEqual(detached, digest)
            self.assertTrue(await store.remove_unreferenced(digest, objects))

        self.assertFalse(stored.path.exists())
