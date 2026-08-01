from __future__ import annotations

import asyncio
import inspect
import json
import math
import unittest
import warnings
from unittest.mock import AsyncMock, patch

from flymail.infrastructure.db.migrations.runner import run_migrations
from flymail.repositories.accounts import AccountRepository, MailAccount
from flymail.repositories.base import AdminContext, TenantContext
from flymail.repositories.mailboxes import Mailbox, MailboxRepository
from flymail.repositories.messages import MessageRepository, RemoteInstanceUpsert
from flymail.repositories.threads import ThreadRepository
from flymail.repositories.users import UserRepository
from flymail.workers.ingestion import MessageIngestionService, RemoteSummary
from tests.v2.mysql_test_case import MySqlIsolatedAsyncioTestCase


class RemoteSummaryContractTests(unittest.TestCase):
    def test_thread_fallback_query_declares_dedicated_index(self):
        source = inspect.getsource(ThreadRepository.find_fallback_thread)
        self.assertIn("FORCE INDEX (idx_messages_subject_fallback)", source)

    def test_non_finite_timestamps_are_rejected(self):
        for field_name, value in (
            ("sent_at", math.nan),
            ("sent_at", math.inf),
            ("received_at", -math.inf),
        ):
            with self.subTest(field=field_name, value=value):
                values = {
                    "remote_uid": 1,
                    "uidvalidity": 1,
                    "sent_at": 1.0,
                    "received_at": 1.0,
                }
                values[field_name] = value
                with self.assertRaisesRegex(ValueError, "finite"):
                    RemoteSummary(**values)


class MessageIngestionTests(MySqlIsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await run_migrations(self.pool)
        await self._clear_tables()
        self.tenant_a, self.account_a, self.inbox_a = await self._create_scope(
            username="ingest-a",
            email="a@example.com",
            provider="generic",
        )
        self.tenant_b, self.account_b, self.inbox_b = await self._create_scope(
            username="ingest-b",
            email="b@example.com",
            provider="generic",
        )
        self.service = MessageIngestionService(self.api_pool)

    async def _clear_tables(self) -> None:
        tables = (
            "thread_projections",
            "thread_messages",
            "message_memberships",
            "message_remote_instances",
            "message_headers",
            "messages",
            "threads",
            "mailboxes",
            "provider_credentials",
            "mail_identities",
            "mail_accounts",
            "user_profiles",
            "user_settings",
            "users",
        )
        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                for table in tables:
                    await cursor.execute(f"DELETE FROM {table}")
            await connection.commit()

    async def _create_scope(
        self,
        *,
        username: str,
        email: str,
        provider: str,
        semantic_key: str = "inbox",
        mailbox_type: str = "folder",
        uidvalidity: int = 10,
    ) -> tuple[TenantContext, MailAccount, Mailbox]:
        async with self.pool.acquire() as connection:
            await connection.begin()
            user = await UserRepository(connection).create_user_for_admin(
                AdminContext("usr_test_admin"),
                username=username,
                password_hash="test-password-hash",
            )
            tenant = TenantContext(user.id)
            account = await AccountRepository(connection).create_account(
                tenant,
                provider_key=provider,
                email=email,
                status="active",
            )
            mailbox = await MailboxRepository(connection).upsert_mailbox(
                tenant,
                account_id=account.id,
                native_key="INBOX",
                native_name="Inbox",
                semantic_key=semantic_key,
                mailbox_type=mailbox_type,
                uidvalidity=uidvalidity,
            )
            await connection.commit()
        return tenant, account, mailbox

    async def _create_account_and_mailbox(
        self,
        tenant: TenantContext,
        *,
        email: str,
        provider: str = "generic",
        native_key: str = "INBOX",
        semantic_key: str = "inbox",
        mailbox_type: str = "folder",
        uidvalidity: int = 10,
    ) -> tuple[MailAccount, Mailbox]:
        async with self.pool.acquire() as connection:
            await connection.begin()
            account = await AccountRepository(connection).create_account(
                tenant,
                provider_key=provider,
                email=email,
                status="active",
            )
            mailbox = await MailboxRepository(connection).upsert_mailbox(
                tenant,
                account_id=account.id,
                native_key=native_key,
                native_name=native_key,
                semantic_key=semantic_key,
                mailbox_type=mailbox_type,
                uidvalidity=uidvalidity,
            )
            await connection.commit()
        return account, mailbox

    def summary(
        self,
        *,
        uid: int,
        uidvalidity: int = 10,
        message_id: str = "",
        in_reply_to: str = "",
        references: tuple[str, ...] = (),
        subject: str = "Subject",
        sender: tuple[str, ...] = ("alice@example.com",),
        recipients: tuple[str, ...] = ("bob@example.com",),
        received_at: float = 1000,
        sent_at: float | None = None,
        size: int = 100,
        flags: frozenset[str] = frozenset(),
        has_attachments: bool = False,
        snippet: str = "preview",
        provider_message_id: str = "",
        provider_thread_id: str = "",
        remote_version: str = "",
    ) -> RemoteSummary:
        return RemoteSummary(
            remote_uid=uid,
            uidvalidity=uidvalidity,
            message_id_header=message_id,
            in_reply_to=in_reply_to,
            references=references,
            subject=subject,
            from_addresses=sender,
            to_addresses=recipients,
            cc_addresses=(),
            sent_at=received_at if sent_at is None else sent_at,
            received_at=received_at,
            size_bytes=size,
            flags=flags,
            has_attachments=has_attachments,
            snippet=snippet,
            provider_message_id=provider_message_id,
            provider_thread_id=provider_thread_id,
            remote_version=remote_version,
        )

    async def rows(self, sql: str, params: tuple = ()) -> list[tuple]:
        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(sql, params)
                return list(await cursor.fetchall())

    async def test_remote_instance_repository_rejects_mixed_batch_scope(self):
        first = RemoteInstanceUpsert(
            canonical_message_key="message:first",
            account_id=self.account_a.id,
            mailbox_id=self.inbox_a.id,
            uidvalidity=10,
            remote_uid=1,
            provider_message_id="",
            provider_thread_id="",
            flags=(),
            is_read=False,
            is_starred=False,
            remote_version="",
            seen_at=1,
        )
        second = RemoteInstanceUpsert(
            canonical_message_key="message:second",
            account_id=self.account_b.id,
            mailbox_id=self.inbox_b.id,
            uidvalidity=10,
            remote_uid=2,
            provider_message_id="",
            provider_thread_id="",
            flags=(),
            is_read=False,
            is_starred=False,
            remote_version="",
            seen_at=1,
        )
        async with self.pool.acquire() as connection:
            await connection.begin()
            with self.assertRaisesRegex(ValueError, "single account and mailbox"):
                await MessageRepository(connection).upsert_remote_instances(
                    self.tenant_a,
                    (first, second),
                    {"message:first": "msg_first", "message:second": "msg_second"},
                    now=1,
                )
            await connection.rollback()

    async def test_user_row_lock_serializes_database_write_phase(self):
        blocker_lease = self.worker_pool.acquire()
        blocker = await blocker_lease.__aenter__()
        task = None
        try:
            await blocker.begin()
            async with blocker.cursor() as cursor:
                await cursor.execute(
                    "SELECT id FROM users WHERE id = %s FOR UPDATE",
                    (self.tenant_a.user_uid,),
                )
                self.assertEqual(str((await cursor.fetchone())[0]), self.tenant_a.user_uid)

            task = asyncio.create_task(
                self.service.ingest_batch(
                    self.account_a,
                    self.inbox_a,
                    (self.summary(uid=5, message_id="<locked@example.com>"),),
                )
            )
            await asyncio.sleep(0.05)
            self.assertFalse(task.done())

            await blocker.rollback()
            result = await asyncio.wait_for(task, timeout=2)
            self.assertEqual(result.messages_touched, 1)
        finally:
            if task is not None and not task.done():
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            await blocker.rollback()
            await blocker_lease.__aexit__(None, None, None)

    async def test_batch_is_atomic_when_projection_refresh_fails(self):
        summaries = (
            self.summary(uid=1, message_id="<atomic-1@example.com>"),
            self.summary(uid=2, message_id="<atomic-2@example.com>"),
        )

        with patch(
            "flymail.repositories.threads.ThreadRepository.refresh_projections",
            new=AsyncMock(side_effect=RuntimeError("projection failed")),
        ):
            with self.assertRaisesRegex(RuntimeError, "projection failed"):
                await self.service.ingest_batch(self.account_a, self.inbox_a, summaries)

        for table in (
            "messages",
            "message_headers",
            "message_remote_instances",
            "message_memberships",
            "threads",
            "thread_messages",
            "thread_projections",
        ):
            with self.subTest(table=table):
                self.assertEqual(await self.scalar(f"SELECT COUNT(*) FROM {table}"), 0)

    async def test_updated_references_rethread_same_message_without_stale_projection(self):
        initial = self.summary(
            uid=6,
            message_id="<late-references-reply@example.com>",
            subject="Re: Late References",
            received_at=900,
            snippet="before references",
        )
        enriched = self.summary(
            uid=6,
            message_id="<late-references-reply@example.com>",
            in_reply_to="<late-references-root@example.com>",
            references=("<late-references-root@example.com>",),
            subject="Re: Late References",
            received_at=900,
            snippet="after references",
        )

        await self.service.ingest_batch(self.account_a, self.inbox_a, (initial,))
        old_thread_id = await self.scalar("SELECT thread_id FROM messages")
        await self.service.ingest_batch(self.account_a, self.inbox_a, (enriched,))
        new_thread_id = await self.scalar("SELECT thread_id FROM messages")

        self.assertNotEqual(old_thread_id, new_thread_id)
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM messages"), 1)
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM message_remote_instances"), 1)
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM thread_messages"), 1)
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM thread_messages WHERE thread_id = %s",
                (old_thread_id,),
            ),
            0,
        )
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM thread_projections WHERE thread_id = %s",
                (old_thread_id,),
            ),
            0,
        )
        self.assertEqual(
            await self.scalar("SELECT COUNT(*) FROM threads WHERE id = %s", (old_thread_id,)),
            0,
        )
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM thread_projections WHERE thread_id = %s",
                (new_thread_id,),
            ),
            1,
        )

    async def test_duplicate_remote_identity_updates_without_duplication(self):
        first = self.summary(
            uid=7,
            message_id="<duplicate@example.com>",
            flags=frozenset(),
            snippet="first",
            remote_version="v1",
        )
        second = self.summary(
            uid=7,
            message_id="<duplicate@example.com>",
            flags=frozenset({"\\Seen", "\\Flagged"}),
            snippet="second",
            remote_version="v2",
        )

        await self.service.ingest_batch(self.account_a, self.inbox_a, (first,))
        result = await self.service.ingest_batch(self.account_a, self.inbox_a, (second,))

        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM messages"), 1)
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM message_remote_instances"), 1)
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM message_memberships"), 1)
        row = (await self.rows(
            """
            SELECT m.snippet, r.is_read, r.is_starred, r.remote_version
            FROM messages m JOIN message_remote_instances r ON r.message_id = m.id
            """
        ))[0]
        self.assertEqual(row, ("second", 1, 1, "v2"))
        self.assertEqual(result.messages_touched, 1)
        self.assertEqual(result.remote_instances_touched, 1)

    async def test_gmail_stable_message_under_two_labels_creates_one_message(self):
        gmail_account, all_mail = await self._create_account_and_mailbox(
            self.tenant_a,
            email="gmail@example.com",
            provider="gmail",
            native_key="[Gmail]/All Mail",
            semantic_key="all_mail",
            mailbox_type="label",
        )
        async with self.pool.acquire() as connection:
            await connection.begin()
            starred = await MailboxRepository(connection).upsert_mailbox(
                self.tenant_a,
                account_id=gmail_account.id,
                native_key="Starred",
                native_name="Starred",
                semantic_key="custom",
                mailbox_type="label",
                uidvalidity=11,
            )
            await connection.commit()

        all_mail_summary = self.summary(
            uid=101,
            uidvalidity=10,
            message_id="<gmail-shared@example.com>",
            provider_message_id="gmail-message-777",
            provider_thread_id="gmail-thread-9",
        )
        starred_summary = self.summary(
            uid=202,
            uidvalidity=11,
            message_id="<gmail-shared@example.com>",
            provider_message_id="gmail-message-777",
            provider_thread_id="gmail-thread-9",
        )

        await self.service.ingest_batch(gmail_account, all_mail, (all_mail_summary,))
        await self.service.ingest_batch(gmail_account, starred, (starred_summary,))

        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM messages"), 1)
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM message_remote_instances"), 2)
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM message_memberships"), 2)
        message_ids = await self.rows(
            "SELECT DISTINCT message_id FROM message_remote_instances ORDER BY message_id"
        )
        self.assertEqual(len(message_ids), 1)

    async def test_cross_account_references_chain_merges_thread_and_projection(self):
        second_account, second_inbox = await self._create_account_and_mailbox(
            self.tenant_a,
            email="a-second@example.com",
            native_key="INBOX",
            semantic_key="inbox",
        )
        root = self.summary(
            uid=1,
            message_id="<thread-root@example.com>",
            subject="Project Update",
            sender=("alice@example.com",),
            recipients=("bob@example.com",),
            received_at=1000,
            snippet="root",
        )
        reply = self.summary(
            uid=2,
            message_id="<thread-reply@example.com>",
            in_reply_to="<thread-root@example.com>",
            references=("<thread-root@example.com>",),
            subject="Re: Project Update",
            sender=("bob@example.com",),
            recipients=("alice@example.com",),
            received_at=2000,
            flags=frozenset({"\\Seen", "\\Flagged"}),
            has_attachments=True,
            snippet="latest reply",
        )

        await self.service.ingest_batch(self.account_a, self.inbox_a, (root,))
        await self.service.ingest_batch(second_account, second_inbox, (reply,))

        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM threads"), 1)
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM messages"), 2)
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM thread_messages"), 2)
        projection = (await self.rows(
            """
            SELECT latest_message_at, subject, latest_snippet, message_count,
                   unread_count, is_starred, has_attachments, account_count
            FROM thread_projections
            WHERE user_uid = %s AND semantic_mailbox = 'inbox'
            """,
            (self.tenant_a.user_uid,),
        ))[0]
        self.assertEqual(projection[0], 2000)
        self.assertEqual(projection[1], "Re: Project Update")
        self.assertEqual(projection[2], "latest reply")
        self.assertEqual(projection[3:], (2, 1, 1, 1, 2))

    async def test_subject_fallback_query_uses_dedicated_index(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", Warning)
            raw_plan = await self.scalar(
                """
            EXPLAIN FORMAT=JSON
            SELECT t.id AS thread_id, t.canonical_thread_key,
                   t.normalized_subject, m.from_json, m.to_json, m.cc_json,
                   m.received_at
            FROM messages m FORCE INDEX (idx_messages_subject_fallback)
            JOIN threads t ON t.id = m.thread_id AND t.user_uid = m.user_uid
            WHERE m.user_uid = %s AND m.normalized_subject = %s
              AND m.thread_id IS NOT NULL
              AND m.received_at BETWEEN %s AND %s
            ORDER BY ABS(m.received_at - %s) ASC,
                     m.received_at DESC, m.id DESC
                LIMIT 100
                """,
                (self.tenant_a.user_uid, "subject", 1, 100, 50),
            )
        plan = json.loads(raw_plan)

        def selected_keys(value):
            if isinstance(value, dict):
                if isinstance(value.get("key"), str):
                    yield value["key"]
                for child in value.values():
                    yield from selected_keys(child)
            elif isinstance(value, list):
                for child in value:
                    yield from selected_keys(child)

        self.assertIn("idx_messages_subject_fallback", set(selected_keys(plan)))

    async def test_subject_fallback_requires_participant_overlap_and_time_window(self):
        first = self.summary(
            uid=11,
            subject="Quarterly Status",
            sender=("alice@example.com",),
            recipients=("bob@example.com",),
            received_at=100_000,
            snippet="first",
        )
        unrelated = self.summary(
            uid=12,
            subject="Re: Quarterly Status",
            sender=("carol@example.com",),
            recipients=("dave@example.com",),
            received_at=100_100,
            snippet="unrelated",
        )
        related = self.summary(
            uid=13,
            subject="Fwd: Quarterly Status",
            sender=("alice@example.com",),
            recipients=("other@example.com",),
            received_at=100_200,
            snippet="related",
        )
        too_late = self.summary(
            uid=14,
            subject="Quarterly Status",
            sender=("alice@example.com",),
            recipients=("bob@example.com",),
            received_at=100_000 + 15 * 24 * 3600,
            snippet="too late",
        )

        for summary in (first, unrelated, related, too_late):
            await self.service.ingest_batch(self.account_a, self.inbox_a, (summary,))

        mapping = dict(await self.rows("SELECT snippet, thread_id FROM messages"))
        self.assertEqual(mapping["first"], mapping["related"])
        self.assertNotEqual(mapping["first"], mapping["unrelated"])
        self.assertNotEqual(mapping["first"], mapping["too late"])
        self.assertEqual(len(set(mapping.values())), 3)

    async def test_same_batch_subject_fallback_uses_participant_overlap(self):
        first = self.summary(
            uid=15,
            subject="Daily Notes",
            sender=("alice@example.com",),
            recipients=("bob@example.com",),
            received_at=200_000,
            snippet="batch first",
        )
        related = self.summary(
            uid=16,
            subject="Re: Daily Notes",
            sender=("bob@example.com",),
            recipients=("alice@example.com",),
            received_at=200_100,
            snippet="batch related",
        )

        await self.service.ingest_batch(
            self.account_a,
            self.inbox_a,
            (first, related),
        )

        mapping = dict(await self.rows(
            "SELECT snippet, thread_id FROM messages WHERE snippet IN ('batch first', 'batch related')"
        ))
        self.assertEqual(mapping["batch first"], mapping["batch related"])
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM threads"), 1)

    async def test_same_message_id_is_isolated_between_users(self):
        shared_a = self.summary(uid=21, message_id="<shared-across-users@example.com>")
        shared_b = self.summary(uid=22, message_id="<shared-across-users@example.com>")

        await self.service.ingest_batch(self.account_a, self.inbox_a, (shared_a,))
        await self.service.ingest_batch(self.account_b, self.inbox_b, (shared_b,))

        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM messages"), 2)
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM threads"), 2)
        rows = await self.rows(
            "SELECT user_uid, COUNT(DISTINCT thread_id) FROM messages GROUP BY user_uid"
        )
        self.assertEqual(
            dict(rows),
            {self.tenant_a.user_uid: 1, self.tenant_b.user_uid: 1},
        )

    async def test_zero_uidvalidity_discovery_does_not_erase_known_value(self):
        old = self.summary(
            uid=30,
            uidvalidity=10,
            message_id="<uidvalidity-zero@example.com>",
        )
        await self.service.ingest_batch(self.account_a, self.inbox_a, (old,))

        async with self.pool.acquire() as connection:
            await connection.begin()
            rediscovered = await MailboxRepository(connection).upsert_mailbox(
                self.tenant_a,
                account_id=self.account_a.id,
                native_key=self.inbox_a.native_key,
                native_name=self.inbox_a.native_name,
                semantic_key="inbox",
                mailbox_type="folder",
                uidvalidity=0,
            )
            await connection.commit()

        self.assertEqual(rediscovered.uidvalidity, 10)
        self.assertNotEqual(rediscovered.sync_status, "reconciling")
        self.assertEqual(
            await self.scalar(
                "SELECT remote_deleted FROM message_remote_instances WHERE remote_uid = 30"
            ),
            0,
        )

    async def test_stale_mailbox_snapshot_uses_current_persisted_uidvalidity(self):
        stale_mailbox = self.inbox_a
        async with self.pool.acquire() as connection:
            await connection.begin()
            await MailboxRepository(connection).upsert_mailbox(
                self.tenant_a,
                account_id=self.account_a.id,
                native_key=self.inbox_a.native_key,
                native_name=self.inbox_a.native_name,
                semantic_key="inbox",
                mailbox_type="folder",
                uidvalidity=20,
            )
            await connection.commit()

        summary = self.summary(
            uid=32,
            uidvalidity=20,
            message_id="<stale-mailbox@example.com>",
        )
        result = await self.service.ingest_batch(
            self.account_a,
            stale_mailbox,
            (summary,),
        )

        self.assertEqual(result.messages_touched, 1)
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM message_remote_instances WHERE uidvalidity = 20"
            ),
            1,
        )

    async def test_disabled_account_is_rejected_before_message_writes(self):
        async with self.pool.acquire() as connection:
            await connection.begin()
            await AccountRepository(connection).update_status(
                self.tenant_a,
                self.account_a.id,
                "disabled",
            )
            await connection.commit()

        with self.assertRaisesRegex(ValueError, "not active"):
            await self.service.ingest_batch(
                self.account_a,
                self.inbox_a,
                (self.summary(uid=33, message_id="<disabled@example.com>"),),
            )
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM messages"), 0)

    async def test_uidvalidity_change_marks_old_instance_and_creates_new_identity(self):
        old = self.summary(
            uid=31,
            uidvalidity=10,
            message_id="<uidvalidity@example.com>",
            remote_version="old",
        )
        await self.service.ingest_batch(self.account_a, self.inbox_a, (old,))

        async with self.pool.acquire() as connection:
            await connection.begin()
            changed_mailbox = await MailboxRepository(connection).upsert_mailbox(
                self.tenant_a,
                account_id=self.account_a.id,
                native_key=self.inbox_a.native_key,
                native_name=self.inbox_a.native_name,
                semantic_key="inbox",
                mailbox_type="folder",
                uidvalidity=20,
            )
            await connection.commit()

        fresh = self.summary(
            uid=31,
            uidvalidity=20,
            message_id="<uidvalidity@example.com>",
            remote_version="fresh",
        )
        await self.service.ingest_batch(self.account_a, changed_mailbox, (fresh,))

        rows = await self.rows(
            """
            SELECT uidvalidity, remote_deleted, remote_version
            FROM message_remote_instances
            WHERE account_id = %s AND mailbox_id = %s
            ORDER BY uidvalidity
            """,
            (self.account_a.id, self.inbox_a.id),
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][0:2], (10, 1))
        self.assertTrue(rows[0][2].startswith("reconcile:uidvalidity:"))
        self.assertEqual(rows[1], (20, 0, "fresh"))
        mailbox_row = (await self.rows(
            "SELECT uidvalidity, sync_status FROM mailboxes WHERE id = %s",
            (self.inbox_a.id,),
        ))[0]
        self.assertEqual(mailbox_row, (20, "reconciling"))

    async def test_batch_result_and_mailbox_counts_are_updated(self):
        summaries = (
            self.summary(uid=41, message_id="<batch-1@example.com>", received_at=10),
            self.summary(
                uid=42,
                message_id="<batch-2@example.com>",
                received_at=20,
                flags=frozenset({"\\Seen"}),
            ),
        )

        result = await self.service.ingest_batch(self.account_a, self.inbox_a, summaries)

        self.assertEqual(result.messages_touched, 2)
        self.assertEqual(result.remote_instances_touched, 2)
        self.assertEqual(result.memberships_touched, 2)
        self.assertEqual(result.threads_touched, 2)
        mailbox_counts = (await self.rows(
            "SELECT total_count, unread_count FROM mailboxes WHERE id = %s",
            (self.inbox_a.id,),
        ))[0]
        self.assertEqual(mailbox_counts, (2, 1))


if __name__ == "__main__":
    unittest.main()
