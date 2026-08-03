"""Production account cleanup preserves shared mail and send audit safely."""

from __future__ import annotations

import tempfile
from pathlib import Path

from flymail.domain.enums import ObjectKind
from flymail.infrastructure.db.migrations.runner import run_migrations
from flymail.infrastructure.object_store.store import ObjectStore
from flymail.infrastructure.security.credentials import CredentialCipher
from flymail.repositories.accounts import AccountRepository, CredentialRepository
from flymail.repositories.base import AdminContext, TenantContext
from flymail.repositories.mailboxes import MailboxRepository
from flymail.repositories.objects import ObjectRepository
from flymail.repositories.users import UserRepository
from flymail.workers.dispatcher import JobContext
from flymail.workers.ingestion import MessageIngestionService, RemoteSummary
from tests.v2.mysql_test_case import MySqlIsolatedAsyncioTestCase


async def _single_chunk(value: bytes):
    yield value


class _FailingCleanupObjectStore(ObjectStore):
    async def remove_unreferenced(self, content_sha256: str, repository) -> bool:
        raise RuntimeError("simulated object cleanup failure")


class AccountCleanupRuntimeTests(MySqlIsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await run_migrations(self.api_pool)
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("SET FOREIGN_KEY_CHECKS=0")
                for table in (
                    "send_attempts",
                    "draft_attachments",
                    "draft_recipients",
                    "draft_versions",
                    "drafts",
                    "content_references",
                    "content_objects",
                    "message_attachments",
                    "message_body_parts",
                    "message_bodies",
                    "body_search_documents",
                    "message_headers",
                    "message_memberships",
                    "message_remote_instances",
                    "thread_projections",
                    "thread_messages",
                    "messages",
                    "threads",
                    "sync_cursors",
                    "mail_operations",
                    "worker_jobs",
                    "account_runtime_state",
                    "mailboxes",
                    "provider_credentials",
                    "mail_identities",
                    "outbound_proxy_configs",
                    "notification_events",
                    "mail_accounts",
                    "user_settings",
                    "user_profiles",
                    "users",
                ):
                    await cursor.execute(f"DELETE FROM {table}")
                await cursor.execute("SET FOREIGN_KEY_CHECKS=1")
            await connection.commit()
        self.temp_dir = tempfile.TemporaryDirectory(prefix="flymail-account-cleanup-")
        root = Path(self.temp_dir.name)
        self.store = ObjectStore(root / "objects", root / ".tmp")
        async with self.api_pool.acquire() as connection:
            await connection.begin()
            self.user = await UserRepository(connection).create_user_for_admin(
                AdminContext("usr_cleanup_admin"),
                username="cleanup-user",
                password_hash="cleanup-test-hash",
            )
            self.tenant = TenantContext(self.user.id)
            accounts = AccountRepository(connection)
            self.account_a = await accounts.create_account(
                self.tenant,
                provider_key="generic",
                email="cleanup-a@example.test",
                status="active",
            )
            self.account_b = await accounts.create_account(
                self.tenant,
                provider_key="generic",
                email="cleanup-b@example.test",
                status="active",
            )
            mailboxes = MailboxRepository(connection)
            self.inbox_a = await mailboxes.upsert_mailbox(
                self.tenant,
                account_id=self.account_a.id,
                native_key="INBOX",
                native_name="Inbox",
                semantic_key="inbox",
                mailbox_type="folder",
                uidvalidity=1,
            )
            self.inbox_b = await mailboxes.upsert_mailbox(
                self.tenant,
                account_id=self.account_b.id,
                native_key="INBOX",
                native_name="Inbox",
                semantic_key="inbox",
                mailbox_type="folder",
                uidvalidity=1,
            )
            cipher = CredentialCipher.from_master_secret("cleanup-runtime-secret")
            await CredentialRepository(connection).store_encrypted(
                self.tenant,
                self.account_a.id,
                credential_type="password",
                value=cipher.encrypt(self.account_a.id, b"mail-secret"),
            )
            await connection.commit()

        ingestion = MessageIngestionService(self.api_pool)
        shared = RemoteSummary(
            remote_uid=10,
            uidvalidity=1,
            message_id_header="<shared-cleanup@example.test>",
            subject="Shared cleanup",
            from_addresses=("sender@example.test",),
            to_addresses=("cleanup@example.test",),
            sent_at=100,
            received_at=100,
            snippet="shared",
        )
        await ingestion.ingest_batch(self.account_a, self.inbox_a, (shared,))
        await ingestion.ingest_batch(
            self.account_b,
            self.inbox_b,
            (
                RemoteSummary(
                    remote_uid=20,
                    uidvalidity=1,
                    message_id_header="<shared-cleanup@example.test>",
                    subject="Shared cleanup",
                    from_addresses=("sender@example.test",),
                    to_addresses=("cleanup@example.test",),
                    sent_at=100,
                    received_at=100,
                    snippet="shared",
                ),
            ),
        )
        await ingestion.ingest_batch(
            self.account_a,
            self.inbox_a,
            (
                RemoteSummary(
                    remote_uid=11,
                    uidvalidity=1,
                    message_id_header="<unique-cleanup@example.test>",
                    subject="Unique cleanup",
                    from_addresses=("sender@example.test",),
                    to_addresses=("cleanup@example.test",),
                    sent_at=101,
                    received_at=101,
                    snippet="unique",
                ),
            ),
        )
        self.shared_message_id = str(
            await self.scalar(
                "SELECT id FROM messages WHERE message_id_header='<shared-cleanup@example.test>'"
            )
        )
        self.unique_message_id = str(
            await self.scalar(
                "SELECT id FROM messages WHERE message_id_header='<unique-cleanup@example.test>'"
            )
        )
        self.shared_thread_id = str(
            await self.scalar(
                "SELECT thread_id FROM messages WHERE id=%s",
                (self.shared_message_id,),
            )
        )
        self.icon_object = await self.store.put_stream(
            ObjectKind.ACCOUNT_ICON,
            _single_chunk(b"account-icon"),
        )
        self.unique_object = await self.store.put_stream(
            ObjectKind.BODY_TEXT,
            _single_chunk(b"unique-body"),
        )
        self.shared_object = await self.store.put_stream(
            ObjectKind.BODY_TEXT,
            _single_chunk(b"shared-body"),
        )
        async with self.api_pool.acquire() as connection:
            await connection.begin()
            objects = ObjectRepository(connection)
            await objects.attach_reference(
                self.icon_object,
                user_uid=self.user.id,
                reference_kind="account_icon",
                reference_id=self.account_a.id,
            )
            await objects.attach_reference(
                self.unique_object,
                user_uid=self.user.id,
                reference_kind="message_body_text",
                reference_id=self.unique_message_id,
            )
            await objects.attach_reference(
                self.shared_object,
                user_uid=self.user.id,
                reference_kind="message_body_text",
                reference_id=self.shared_message_id,
            )
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "UPDATE mail_accounts SET icon_object_sha256=%s WHERE id=%s",
                    (self.icon_object.content_sha256, self.account_a.id),
                )
                await cursor.execute(
                    """
                    UPDATE message_bodies
                    SET text_object_sha256=%s, state='ready'
                    WHERE message_id=%s AND user_uid=%s
                    """,
                    (
                        self.unique_object.content_sha256,
                        self.unique_message_id,
                        self.user.id,
                    ),
                )
                await cursor.execute(
                    """
                    UPDATE message_bodies
                    SET text_object_sha256=%s, state='ready'
                    WHERE message_id=%s AND user_uid=%s
                    """,
                    (
                        self.shared_object.content_sha256,
                        self.shared_message_id,
                        self.user.id,
                    ),
                )
                await cursor.execute(
                    """
                    INSERT INTO send_attempts (
                        id, user_uid, draft_id, operation_id, account_id,
                        message_id_header, attempt_number, status,
                        smtp_response_code, safe_response,
                        started_at, finished_at, created_at
                    ) VALUES ('send_cleanup_audit', %s, 'draft_cleanup_audit',
                              'op_cleanup_audit', %s,
                              '<audit-cleanup@example.test>', 1, 'sent',
                              250, 'accepted', 1, 2, 1)
                    """,
                    (self.user.id, self.account_a.id),
                )
            await connection.commit()
        async with self.api_pool.acquire() as connection:
            await connection.begin()
            await AccountRepository(connection).update_status(
                self.tenant,
                self.account_a.id,
                "deleting",
            )
            await connection.commit()

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()
        await super().asyncTearDown()

    async def test_cleanup_removes_account_orphans_preserves_shared_mail_and_send_audit(self):
        from flymail.workers.account_cleanup import AccountDataCleanupGateway

        from flymail.workers.accounts import AccountCleanupHandler

        gateway = AccountDataCleanupGateway(self.worker_pool, self.store)
        outcome = await AccountCleanupHandler(self.worker_pool, gateway)(
            JobContext(
                job_id="job_cleanup_runtime",
                user_uid=self.user.id,
                account_id=None,
                provider_key=None,
                queue_name="maintenance",
                worker_id="wrk_cleanup_runtime",
                attempt_count=1,
                stop_event=__import__("asyncio").Event(),
            ),
            {"account_id": self.account_a.id},
        )
        self.assertEqual(outcome.action, "complete")

        self.assertEqual(
            await self.scalar("SELECT COUNT(*) FROM mail_accounts WHERE id=%s", (self.account_a.id,)),
            0,
        )
        self.assertEqual(
            await self.scalar("SELECT COUNT(*) FROM provider_credentials WHERE account_id=%s", (self.account_a.id,)),
            0,
        )
        self.assertEqual(
            await self.scalar("SELECT COUNT(*) FROM mailboxes WHERE account_id=%s", (self.account_a.id,)),
            0,
        )
        self.assertEqual(
            await self.scalar("SELECT COUNT(*) FROM messages WHERE id=%s", (self.unique_message_id,)),
            0,
        )
        self.assertEqual(
            await self.scalar("SELECT COUNT(*) FROM messages WHERE id=%s", (self.shared_message_id,)),
            1,
        )
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM message_remote_instances WHERE message_id=%s",
                (self.shared_message_id,),
            ),
            1,
        )
        self.assertEqual(
            await self.scalar(
                "SELECT account_count FROM thread_projections WHERE thread_id=%s AND semantic_mailbox='inbox'",
                (self.shared_thread_id,),
            ),
            1,
        )
        self.assertEqual(
            await self.scalar("SELECT COUNT(*) FROM send_attempts WHERE id='send_cleanup_audit'"),
            1,
        )
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM outbox_events WHERE aggregate_id=%s AND event_type='account.cleanup_completed'",
                (self.account_a.id,),
            ),
            1,
        )
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM content_references WHERE content_sha256=%s",
                (self.unique_object.content_sha256,),
            ),
            0,
        )
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM content_references WHERE content_sha256=%s",
                (self.icon_object.content_sha256,),
            ),
            0,
        )
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM content_references WHERE content_sha256=%s",
                (self.shared_object.content_sha256,),
            ),
            1,
        )
        self.assertFalse(self.unique_object.path.exists())
        self.assertFalse(self.icon_object.path.exists())
        self.assertTrue(self.shared_object.path.exists())

    async def test_cleanup_completes_when_physical_object_removal_fails(self):
        from flymail.workers.account_cleanup import AccountDataCleanupGateway
        from flymail.workers.accounts import AccountCleanupHandler

        failing_store = _FailingCleanupObjectStore(
            self.store.root,
            self.store.temp_root,
        )
        gateway = AccountDataCleanupGateway(self.worker_pool, failing_store)
        outcome = await AccountCleanupHandler(self.worker_pool, gateway)(
            JobContext(
                job_id="job_cleanup_object_failure",
                user_uid=self.user.id,
                account_id=None,
                provider_key=None,
                queue_name="maintenance",
                worker_id="wrk_cleanup_object_failure",
                attempt_count=1,
                stop_event=__import__("asyncio").Event(),
            ),
            {"account_id": self.account_a.id},
        )

        self.assertEqual(outcome.action, "complete")
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM mail_accounts WHERE id=%s",
                (self.account_a.id,),
            ),
            0,
        )
        self.assertTrue(self.unique_object.path.exists())
        self.assertTrue(self.icon_object.path.exists())
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM content_objects WHERE content_sha256 IN (%s, %s)",
                (
                    self.unique_object.content_sha256,
                    self.icon_object.content_sha256,
                ),
            ),
            2,
        )


if __name__ == "__main__":
    import unittest

    unittest.main()
