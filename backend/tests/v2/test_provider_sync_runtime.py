"""Production provider synchronization against real MySQL and fake IMAP."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from imapclient.imap_utf7 import encode as encode_imap_utf7

from flymail.infrastructure.db.migrations.runner import run_migrations
from flymail.infrastructure.object_store.store import ObjectStore
from flymail.infrastructure.security.credentials import CredentialCipher
from flymail.repositories.accounts import AccountRepository, CredentialRepository
from flymail.repositories.base import AdminContext, TenantContext
from flymail.repositories.users import UserRepository
from flymail.workers.content_fetch import ContentFetchService, ContentJobPublisher
from flymail.workers.dispatcher import JobContext
from tests.v2.mysql_test_case import MySqlIsolatedAsyncioTestCase


class FakeSyncImapClient:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.fetch_count = 0

    def list_folders(self):
        self.calls.append(("list_folders",))
        return [
            ((b"\\Inbox",), b"/", b"INBOX"),
            ((b"\\Sent",), b"/", b"Sent"),
            ((b"\\HasNoChildren",), b"/", encode_imap_utf7("项目")),
        ]

    def folder_status(self, folder, what=None):
        self.calls.append(("folder_status", folder, tuple(what or ())))
        return {
            b"UIDVALIDITY": 1,
            b"HIGHESTMODSEQ": 2,
            b"MESSAGES": 1,
            b"UNSEEN": 1,
        }

    def select_folder(self, folder, readonly=False):
        self.calls.append(("select_folder", folder, readonly))
        return {
            b"UIDVALIDITY": 1,
            b"HIGHESTMODSEQ": 2,
            b"EXISTS": 1,
        }

    def search(self, criteria, charset=None):
        self.calls.append(("search", criteria, charset))
        return [101]

    def fetch(self, messages, data, modifiers=None):
        self.fetch_count += 1
        self.calls.append(("fetch", tuple(messages), tuple(data), modifiers))
        return {
            101: {
                b"FLAGS": (b"\\Seen",) if self.fetch_count > 1 else (),
                b"INTERNALDATE": datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc),
                b"RFC822.SIZE": 123,
                b"BODY[HEADER.FIELDS (MESSAGE-ID IN-REPLY-TO REFERENCES SUBJECT FROM TO CC DATE)]": (
                    b"Message-ID: <sync-runtime@example.test>\r\n"
                    b"Subject: Runtime sync\r\n"
                    b"From: Sender <sender@example.test>\r\n"
                    b"To: Receiver <receiver@example.test>\r\n"
                    b"Date: Sun, 02 Aug 2026 12:00:00 +0000\r\n\r\n"
                ),
                b"BODYSTRUCTURE": (
                    b"TEXT",
                    b"PLAIN",
                    (b"CHARSET", b"UTF-8"),
                    None,
                    None,
                    b"7BIT",
                    12,
                    1,
                ),
                b"MODSEQ": (2,),
                b"X-GM-MSGID": 12345,
                b"X-GM-THRID": 67890,
                b"X-GM-LABELS": (b"INBOX", encode_imap_utf7("项目")),
            }
        }


class FakeSyncImapSession:
    def __init__(self, client: FakeSyncImapClient) -> None:
        self.client = client

    def connect(self):
        return self.client

    def close(self):
        return None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        self.close()


class ProviderSyncRuntimeTests(MySqlIsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await run_migrations(self.api_pool)
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("SET FOREIGN_KEY_CHECKS=0")
                for table in (
                    "message_body_parts",
                    "message_bodies",
                    "body_search_documents",
                    "message_memberships",
                    "message_remote_instances",
                    "thread_messages",
                    "thread_projections",
                    "messages",
                    "threads",
                    "sync_cursors",
                    "mailboxes",
                    "provider_credentials",
                    "mail_accounts",
                    "user_settings",
                    "user_profiles",
                    "users",
                ):
                    await cursor.execute(f"DELETE FROM {table}")
                await cursor.execute("SET FOREIGN_KEY_CHECKS=1")
            await connection.commit()
        async with self.api_pool.acquire() as connection:
            await connection.begin()
            self.user = await UserRepository(connection).create_user_for_admin(
                AdminContext("usr_sync_admin"),
                username="sync-runtime-user",
                password_hash="sync-runtime-hash",
            )
            self.tenant = TenantContext(self.user.id)
            self.account = await AccountRepository(connection).create_account(
                self.tenant,
                provider_key="gmail",
                email="sync-runtime@example.test",
                status="active",
            )
            cipher = CredentialCipher.from_master_secret("sync-runtime-session-secret")
            await CredentialRepository(connection).store_encrypted(
                self.tenant,
                self.account.id,
                credential_type="password",
                value=cipher.encrypt(self.account.id, b"mail-password"),
            )
            await connection.commit()

        from flymail.providers.runtime import ProductionProviderRuntime

        settings = self.settings("worker")
        settings = type(settings)(
            role=settings.role,
            database_url=settings.database_url,
            data_dir=Path("/tmp/flymail-v2-sync-runtime"),
            object_dir=Path("/tmp/flymail-v2-sync-runtime/objects"),
            object_tmp_dir=Path("/tmp/flymail-v2-sync-runtime/.tmp"),
            session_secret="sync-runtime-session-secret",
            db_pool_name=settings.db_pool_name,
            db_min_connections=settings.db_min_connections,
            db_max_connections=settings.db_max_connections,
        )
        self.client = FakeSyncImapClient()
        self.runtime = ProductionProviderRuntime(
            self.worker_pool,
            settings,
            endpoint_resolver=lambda _host, _port: ("8.8.8.8",),
            imap_session_factory=lambda _loaded: FakeSyncImapSession(self.client),
        )
        store = ObjectStore(settings.object_dir, settings.object_tmp_dir)
        content = ContentFetchService(
            self.worker_pool,
            store,
            self.runtime,
            ContentJobPublisher(self.worker_pool),
            body_limit_bytes=1024 * 1024,
            attachment_limit_bytes=1024 * 1024,
            partial_chunk_bytes=64 * 1024,
        )
        self.runtime.bind_content_service(content)
        self.context = JobContext(
            job_id="job_sync_runtime_1",
            user_uid=self.user.id,
            account_id=self.account.id,
            provider_key="gmail",
            queue_name="history",
            worker_id="wrk_sync_runtime_1",
            attempt_count=1,
            stop_event=asyncio.Event(),
        )

    async def test_mailbox_refresh_initial_and_idempotent_incremental_sync(self):
        refresh = await self.runtime.synchronize(
            self.context,
            {"account_id": self.account.id},
            job_kind="sync.mailbox_refresh",
        )
        self.assertEqual(refresh.action, "complete")
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "SELECT id, native_key, semantic_key FROM mailboxes WHERE account_id=%s ORDER BY native_key",
                    (self.account.id,),
                )
                mailboxes = list(await cursor.fetchall())
        self.assertEqual(
            [(row[1], row[2]) for row in mailboxes],
            [("INBOX", "inbox"), ("Sent", "sent"), ("项目", "custom")],
        )
        inbox_id = str(next(row[0] for row in mailboxes if row[1] == "INBOX"))

        initial = await self.runtime.synchronize(
            self.context,
            {"account_id": self.account.id, "mailbox_id": inbox_id},
            job_kind="sync.initial",
        )
        self.assertEqual(initial.action, "complete")
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM messages"), 1)
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM threads"), 1)
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM message_remote_instances"), 1)
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM message_memberships"), 2)
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM message_body_parts"), 1)
        self.assertEqual(
            await self.scalar(
                "SELECT last_uid FROM sync_cursors WHERE account_id=%s AND mailbox_id=%s AND phase='summary'",
                (self.account.id, inbox_id),
            ),
            101,
        )

        incremental = await self.runtime.synchronize(
            self.context,
            {"account_id": self.account.id, "mailbox_id": inbox_id},
            job_kind="sync.incremental",
        )
        self.assertEqual(incremental.action, "complete")
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM messages"), 1)
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM message_remote_instances"), 1)
        self.assertEqual(self.client.fetch_count, 1)

        reconcile = await self.runtime.synchronize(
            self.context,
            {"account_id": self.account.id, "mailbox_id": inbox_id},
            job_kind="sync.reconcile",
        )
        self.assertEqual(reconcile.action, "complete")
        self.assertEqual(self.client.fetch_count, 2)
        self.assertEqual(
            await self.scalar(
                "SELECT is_read FROM message_remote_instances WHERE account_id=%s AND remote_uid=101",
                (self.account.id,),
            ),
            1,
        )


if __name__ == "__main__":
    import unittest

    unittest.main()
