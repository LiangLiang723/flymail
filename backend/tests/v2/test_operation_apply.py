"""Local-first mail operation, conflict, delete, and undo contracts."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

from flymail.domain.errors import ConflictError, NotFoundError, RetryableError
from flymail.domain.operations import (
    OperationKind,
    RemoteApplyResult,
    RemoteOperationState,
)
from flymail.infrastructure.db.migrations.runner import run_migrations
from flymail.providers.registry import ProviderRegistry
from flymail.repositories.base import TenantContext
from flymail.repositories.jobs import LeasedJob
from flymail.repositories.operations import OperationRepository
from flymail.repositories.threads import ThreadRepository
from flymail.workers.dispatcher import WorkerDispatcher
from flymail.workers.operation_apply import (
    OperationApplyHandler,
    OperationService,
)
from tests.v2.mysql_test_case import MySqlIsolatedAsyncioTestCase


class FakeOperationGateway:
    def __init__(self) -> None:
        self.states: dict[str, RemoteOperationState | None] = {}
        self.results: dict[str, RemoteApplyResult | BaseException] = {}
        self.commands = []

    async def observe(self, operation):
        return self.states.get(operation.remote_instance_id)

    async def apply(self, command):
        self.commands.append(command)
        result = self.results.get(command.remote_instance_id)
        if isinstance(result, BaseException):
            raise result
        return result or RemoteApplyResult(
            remote_version=f"{command.expected_remote_version}:applied",
        )


class OperationApplyTests(MySqlIsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await run_migrations(self.api_pool)
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                for table in (
                    "outbox_events",
                    "job_attempts",
                    "worker_jobs",
                    "mail_operations",
                    "thread_projections",
                    "thread_messages",
                    "message_memberships",
                    "message_remote_instances",
                    "messages",
                    "threads",
                    "mailboxes",
                    "account_runtime_state",
                    "mail_accounts",
                    "users",
                ):
                    await cursor.execute(f"DELETE FROM {table}")
                await cursor.execute(
                    """
                    INSERT INTO users (
                        id, username, password_hash, role, enabled,
                        password_version, created_at, updated_at
                    ) VALUES ('usr_owner', 'owner', 'test-hash', 'user', 1, 1, 1, 1)
                    """
                )
                await cursor.executemany(
                    """
                    INSERT INTO mail_accounts (
                        id, user_uid, provider_key, email, normalized_email,
                        display_name, remark, group_name, status,
                        endpoint_config, icon_mode, icon_value,
                        icon_object_sha256, poll_interval_seconds,
                        created_at, updated_at
                    ) VALUES (%s, 'usr_owner', %s, %s, %s, '', '', '', 'active',
                              NULL, 'provider', '', NULL, 300, 1, 1)
                    """,
                    [
                        ("acc_generic", "generic", "generic@example.com", "generic@example.com"),
                        ("acc_gmail", "gmail", "gmail@example.com", "gmail@example.com"),
                    ],
                )
                await cursor.executemany(
                    """
                    INSERT INTO mailboxes (
                        id, user_uid, account_id, native_key, native_name,
                        semantic_key, mailbox_type, delimiter_value,
                        attributes_json, uidvalidity, highest_modseq,
                        total_count, unread_count, sync_status,
                        created_at, updated_at
                    ) VALUES (%s, 'usr_owner', %s, %s, %s, %s, %s, '/',
                              NULL, 1, 0, 0, 0, 'ready', 1, 1)
                    """,
                    [
                        ("mb_g_inbox", "acc_generic", "INBOX", "Inbox", "inbox", "folder"),
                        ("mb_g_archive", "acc_generic", "Archive", "Archive", "archive", "folder"),
                        ("mb_g_trash", "acc_generic", "Trash", "Trash", "trash", "folder"),
                        ("mb_m_inbox", "acc_gmail", "\\Inbox", "Inbox", "inbox", "label"),
                        ("mb_m_all", "acc_gmail", "\\All", "All Mail", "all_mail", "label"),
                        ("mb_m_trash", "acc_gmail", "\\Trash", "Trash", "trash", "label"),
                        ("mb_m_label", "acc_gmail", "Project", "Project", "custom", "label"),
                    ],
                )
            await connection.commit()
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO threads (
                        id, user_uid, canonical_thread_key,
                        normalized_subject, created_at, updated_at
                    ) VALUES ('thr_one', 'usr_owner', 'key:thr_one',
                              'subject', 1, 1)
                    """
                )
            await connection.commit()
        self.tenant = TenantContext("usr_owner")
        self.registry = ProviderRegistry.default()
        self.service = OperationService(self.api_pool, self.registry)
        self.gateway = FakeOperationGateway()
        self.handler = OperationApplyHandler(
            self.worker_pool,
            self.gateway,
            self.registry,
        )
        await self._create_message(
            thread_id="thr_one",
            message_id="msg_generic",
            remote_id="rmi_generic",
            account_id="acc_generic",
            mailbox_ids=("mb_g_inbox",),
            remote_version="v1",
            is_read=False,
            is_starred=False,
            received_at=10,
        )
        await self._create_message(
            thread_id="thr_one",
            message_id="msg_gmail",
            remote_id="rmi_gmail",
            account_id="acc_gmail",
            mailbox_ids=("mb_m_inbox", "mb_m_all"),
            remote_version="g1",
            is_read=False,
            is_starred=False,
            received_at=20,
        )
        async with self.api_pool.acquire() as connection:
            await connection.begin()
            await ThreadRepository(connection).refresh_projections(
                self.tenant,
                ("thr_one",),
                now=30,
            )
            await connection.commit()

    async def _create_message(
        self,
        *,
        thread_id: str,
        message_id: str,
        remote_id: str,
        account_id: str,
        mailbox_ids: tuple[str, ...],
        remote_version: str,
        is_read: bool,
        is_starred: bool,
        received_at: float,
    ) -> None:
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO messages (
                        id, user_uid, canonical_message_key, message_id_header,
                        thread_id, subject, normalized_subject, from_json,
                        to_json, cc_json, sent_at, received_at, size_bytes,
                        has_attachments, snippet, body_state, search_state,
                        created_at, updated_at
                    ) VALUES (%s, 'usr_owner', %s, %s, %s, 'Subject', 'subject',
                              '["sender@example.com"]', '["owner@example.com"]', '[]',
                              %s, %s, 10, 0, 'snippet', 'not_requested', 'metadata', 1, 1)
                    """,
                    (
                        message_id,
                        f"key:{message_id}",
                        f"<{message_id}@example.test>",
                        thread_id,
                        received_at,
                        received_at,
                    ),
                )
                await cursor.execute(
                    """
                    INSERT INTO thread_messages (
                        thread_id, message_id, user_uid, parent_message_id,
                        relation_source, position_hint, created_at
                    ) VALUES (%s, %s, 'usr_owner', NULL, 'headers', %s, 1)
                    """,
                    (thread_id, message_id, int(received_at)),
                )
                first_mailbox = mailbox_ids[0]
                await cursor.execute(
                    """
                    INSERT INTO message_remote_instances (
                        id, user_uid, account_id, mailbox_id, message_id,
                        uidvalidity, remote_uid, provider_message_id,
                        provider_thread_id, flags_json, is_read, is_starred,
                        remote_version, remote_deleted, last_seen_at,
                        created_at, updated_at
                    ) VALUES (%s, 'usr_owner', %s, %s, %s, 1, %s, '', '', '[]',
                              %s, %s, %s, 0, %s, 1, 1)
                    """,
                    (
                        remote_id,
                        account_id,
                        first_mailbox,
                        message_id,
                        int(received_at),
                        1 if is_read else 0,
                        1 if is_starred else 0,
                        remote_version,
                        received_at,
                    ),
                )
                for mailbox_id in mailbox_ids:
                    kind = "label" if account_id == "acc_gmail" else "folder"
                    await cursor.execute(
                        """
                        INSERT INTO message_memberships (
                            remote_instance_id, mailbox_id, user_uid,
                            membership_kind, provider_label,
                            created_at, updated_at
                        ) VALUES (%s, %s, 'usr_owner', %s, '', 1, 1)
                        """,
                        (remote_id, mailbox_id, kind),
                    )
            await connection.commit()

    async def row(self, sql: str, params: tuple = ()):
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(sql, params)
                return await cursor.fetchone()

    async def rows(self, sql: str, params: tuple = ()):
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(sql, params)
                return await cursor.fetchall()

    async def test_local_projection_operation_outbox_and_job_are_atomic(self):
        with patch.object(
            ThreadRepository,
            "refresh_projections",
            new=AsyncMock(side_effect=RuntimeError("projection failed")),
        ):
            with self.assertRaises(RuntimeError):
                await self.service.record_local_intent(
                    self.tenant,
                    remote_instance_id="rmi_generic",
                    kind=OperationKind.SET_READ,
                    desired_state={"value": True},
                    idempotency_key="read-atomic",
                    now=100,
                )
        self.assertEqual(
            await self.row(
                "SELECT is_read, is_starred FROM message_remote_instances WHERE id = 'rmi_generic'"
            ),
            (0, 0),
        )
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM mail_operations"), 0)
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM outbox_events"), 0)
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM worker_jobs"), 0)

    async def test_read_and_starred_fields_merge_independently_in_local_projection(self):
        await self.service.record_local_intent(
            self.tenant,
            remote_instance_id="rmi_generic",
            kind=OperationKind.SET_READ,
            desired_state={"value": True},
            idempotency_key="read-one",
            now=101,
        )
        self.assertEqual(
            await self.row(
                "SELECT is_read, is_starred FROM message_remote_instances WHERE id = 'rmi_generic'"
            ),
            (1, 0),
        )
        await self.service.record_local_intent(
            self.tenant,
            remote_instance_id="rmi_generic",
            kind=OperationKind.SET_STARRED,
            desired_state={"value": True},
            idempotency_key="star-one",
            now=102,
        )
        self.assertEqual(
            await self.row(
                "SELECT is_read, is_starred FROM message_remote_instances WHERE id = 'rmi_generic'"
            ),
            (1, 1),
        )

    async def test_later_move_supersedes_older_pending_move(self):
        first = await self.service.record_local_intent(
            self.tenant,
            remote_instance_id="rmi_generic",
            kind=OperationKind.MOVE,
            desired_state={"mailbox_id": "mb_g_archive"},
            idempotency_key="move-one",
            now=103,
        )
        second = await self.service.record_local_intent(
            self.tenant,
            remote_instance_id="rmi_generic",
            kind=OperationKind.MOVE,
            desired_state={"mailbox_id": "mb_g_trash"},
            idempotency_key="move-two",
            now=104,
        )
        self.assertNotEqual(first, second)
        self.assertEqual(
            await self.row(
                "SELECT status, last_error_class FROM mail_operations WHERE id = %s",
                (first,),
            ),
            ("cancelled", "Superseded"),
        )
        self.assertEqual(
            await self.scalar(
                "SELECT mailbox_id FROM message_memberships WHERE remote_instance_id = 'rmi_generic'"
            ),
            "mb_g_trash",
        )

    async def test_gmail_archive_removes_inbox_but_preserves_all_mail(self):
        operation_id = await self.service.record_local_intent(
            self.tenant,
            remote_instance_id="rmi_gmail",
            kind=OperationKind.ARCHIVE,
            desired_state={},
            idempotency_key="gmail-archive",
            now=105,
        )
        self.assertEqual(
            tuple(row[0] for row in await self.rows(
                "SELECT mailbox_id FROM message_memberships WHERE remote_instance_id = 'rmi_gmail' ORDER BY mailbox_id"
            )),
            ("mb_m_all",),
        )
        desired = json.loads(str(await self.scalar(
            "SELECT desired_state FROM mail_operations WHERE id = %s",
            (operation_id,),
        )))
        self.assertEqual(desired["remote_action"], "remove_label")
        self.assertEqual(desired["target_native_key"], "\\Inbox")

    async def test_generic_archive_moves_to_mapped_archive_mailbox(self):
        operation_id = await self.service.record_local_intent(
            self.tenant,
            remote_instance_id="rmi_generic",
            kind=OperationKind.ARCHIVE,
            desired_state={},
            idempotency_key="generic-archive",
            now=106,
        )
        self.assertEqual(
            await self.scalar(
                "SELECT mailbox_id FROM message_memberships WHERE remote_instance_id = 'rmi_generic'"
            ),
            "mb_g_archive",
        )
        desired = json.loads(str(await self.scalar(
            "SELECT desired_state FROM mail_operations WHERE id = %s",
            (operation_id,),
        )))
        self.assertEqual(desired["remote_action"], "move")
        self.assertEqual(desired["target_native_key"], "Archive")

    async def test_two_stage_delete_requires_trash_then_explicit_confirmation(self):
        trash_operation_id = await self.service.record_local_intent(
            self.tenant,
            remote_instance_id="rmi_generic",
            kind=OperationKind.TRASH,
            desired_state={},
            idempotency_key="trash-one",
            now=107,
        )
        self.assertEqual(
            await self.scalar(
                "SELECT mailbox_id FROM message_memberships WHERE remote_instance_id = 'rmi_generic'"
            ),
            "mb_g_trash",
        )
        with self.assertRaises(ConflictError):
            await self.service.record_local_intent(
                self.tenant,
                remote_instance_id="rmi_generic",
                kind=OperationKind.DELETE_PERMANENT,
                desired_state={},
                idempotency_key="delete-no-confirm",
                confirm_permanent=False,
                now=108,
            )
        with self.assertRaises(ConflictError):
            await self.service.record_local_intent(
                self.tenant,
                remote_instance_id="rmi_generic",
                kind=OperationKind.DELETE_PERMANENT,
                desired_state={},
                idempotency_key="delete-before-trash-sync",
                confirm_permanent=True,
                now=108.5,
            )
        self.gateway.states["rmi_generic"] = RemoteOperationState(
            remote_version="v1",
            is_read=False,
            is_starred=False,
            mailbox_native_keys=("INBOX",),
        )
        trash_result = await self.handler.handle(
            self.tenant,
            trash_operation_id,
            now=108.75,
        )
        self.assertEqual(trash_result.outcome, "applied")
        operation_id = await self.service.record_local_intent(
            self.tenant,
            remote_instance_id="rmi_generic",
            kind=OperationKind.DELETE_PERMANENT,
            desired_state={},
            idempotency_key="delete-confirmed",
            confirm_permanent=True,
            now=109,
        )
        self.assertEqual(
            await self.row(
                "SELECT remote_deleted, COUNT(m.mailbox_id) FROM message_remote_instances r LEFT JOIN message_memberships m ON m.remote_instance_id = r.id WHERE r.id = 'rmi_generic' GROUP BY r.remote_deleted"
            ),
            (1, 0),
        )
        desired = json.loads(str(await self.scalar(
            "SELECT desired_state FROM mail_operations WHERE id = %s",
            (operation_id,),
        )))
        self.assertTrue(desired["confirmed"])

    async def test_remote_missing_is_terminal_success_without_retry(self):
        operation_id = await self.service.record_local_intent(
            self.tenant,
            remote_instance_id="rmi_generic",
            kind=OperationKind.SET_READ,
            desired_state={"value": True},
            idempotency_key="missing-remote",
            now=110,
        )
        self.gateway.states["rmi_generic"] = None
        result = await self.handler.handle(self.tenant, operation_id, now=111)
        self.assertEqual(result.outcome, "terminal_missing")
        self.assertEqual(
            await self.row(
                "SELECT status, last_error_class FROM mail_operations WHERE id = %s",
                (operation_id,),
            ),
            ("synced", "RemoteMissing"),
        )

    async def test_concurrent_same_idempotency_key_creates_one_operation_and_job(self):
        first, second = await asyncio.gather(
            self.service.record_local_intent(
                self.tenant,
                remote_instance_id="rmi_generic",
                kind=OperationKind.SET_READ,
                desired_state={"value": True},
                idempotency_key="concurrent-read",
                now=111.1,
            ),
            self.service.record_local_intent(
                self.tenant,
                remote_instance_id="rmi_generic",
                kind=OperationKind.SET_READ,
                desired_state={"value": True},
                idempotency_key="concurrent-read",
                now=111.2,
            ),
        )
        self.assertEqual(first, second)
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM mail_operations"), 1)
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM worker_jobs"), 1)
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM outbox_events"), 1)

    async def test_cross_tenant_remote_id_is_indistinguishable_from_missing(self):
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO users (
                        id, username, password_hash, role, enabled,
                        password_version, created_at, updated_at
                    ) VALUES ('usr_other', 'other', 'test-hash', 'user', 1, 1, 1, 1)
                    """
                )
            await connection.commit()
        with self.assertRaises(NotFoundError):
            await self.service.record_local_intent(
                TenantContext("usr_other"),
                remote_instance_id="rmi_generic",
                kind=OperationKind.SET_READ,
                desired_state={"value": True},
                idempotency_key="cross-tenant-read",
                now=111.3,
            )
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM mail_operations"), 0)
        self.assertEqual(
            await self.scalar(
                "SELECT is_read FROM message_remote_instances WHERE id = 'rmi_generic'"
            ),
            0,
        )

    async def test_database_finish_failure_is_not_misclassified_as_remote_error(self):
        operation_id = await self.service.record_local_intent(
            self.tenant,
            remote_instance_id="rmi_generic",
            kind=OperationKind.SET_READ,
            desired_state={"value": True},
            idempotency_key="finish-database-failure",
            now=111.35,
        )
        self.gateway.states["rmi_generic"] = RemoteOperationState(
            remote_version="v1",
            is_read=False,
            is_starred=False,
            mailbox_native_keys=("INBOX",),
        )
        with patch.object(
            self.handler,
            "_finish",
            new=AsyncMock(side_effect=RuntimeError("database unavailable")),
        ):
            with self.assertRaises(RuntimeError):
                await self.handler.handle(self.tenant, operation_id, now=111.36)
        self.assertEqual(
            await self.scalar("SELECT status FROM mail_operations WHERE id = %s", (operation_id,)),
            "applying",
        )

    async def test_handler_registers_directly_with_worker_dispatcher(self):
        operation_id = await self.service.record_local_intent(
            self.tenant,
            remote_instance_id="rmi_generic",
            kind=OperationKind.SET_READ,
            desired_state={"value": True},
            idempotency_key="dispatcher-read",
            now=111.37,
        )
        self.gateway.states["rmi_generic"] = RemoteOperationState(
            remote_version="v1",
            is_read=False,
            is_starred=False,
            mailbox_native_keys=("INBOX",),
        )
        dispatcher = WorkerDispatcher()
        dispatcher.register("mail.operation.apply", self.handler)
        outcome = await dispatcher.dispatch(
            LeasedJob(
                id="job_operation_dispatch",
                user_uid="usr_owner",
                account_id="acc_generic",
                provider_key="generic",
                queue_name="operations",
                job_kind="mail.operation.apply",
                priority=20,
                available_at=111.37,
                lease_owner="worker_test",
                lease_token="lease_operation_dispatch",
                lease_expires_at=171.37,
                attempt_count=1,
                max_attempts=10,
                dedupe_key=f"mail-operation:{operation_id}",
                payload={"operation_id": operation_id},
            ),
            stop_event=asyncio.Event(),
        )
        self.assertEqual(outcome.action, "complete")
        self.assertEqual(
            await self.scalar("SELECT status FROM mail_operations WHERE id = %s", (operation_id,)),
            "synced",
        )

    async def test_unexpected_gateway_error_returns_retry_and_does_not_leave_applying(self):
        operation_id = await self.service.record_local_intent(
            self.tenant,
            remote_instance_id="rmi_generic",
            kind=OperationKind.SET_READ,
            desired_state={"value": True},
            idempotency_key="unexpected-gateway",
            now=111.4,
        )
        self.gateway.states["rmi_generic"] = RemoteOperationState(
            remote_version="v1",
            is_read=False,
            is_starred=False,
            mailbox_native_keys=("INBOX",),
        )
        self.gateway.results["rmi_generic"] = RuntimeError("contains internal detail")
        result = await self.handler.handle(self.tenant, operation_id, now=111.5)
        self.assertEqual(result.outcome, "retry")
        self.assertEqual(
            await self.row(
                "SELECT status, last_error_class, last_error_message FROM mail_operations WHERE id = %s",
                (operation_id,),
            ),
            (
                "retry_wait",
                "UnexpectedRemoteError",
                "remote operation will be retried",
            ),
        )

    async def test_same_idempotency_key_with_different_intent_is_rejected(self):
        await self.service.record_local_intent(
            self.tenant,
            remote_instance_id="rmi_generic",
            kind=OperationKind.SET_READ,
            desired_state={"value": True},
            idempotency_key="mismatched-read",
            now=111.6,
        )
        with self.assertRaises(ConflictError):
            await self.service.record_local_intent(
                self.tenant,
                remote_instance_id="rmi_generic",
                kind=OperationKind.SET_READ,
                desired_state={"value": False},
                idempotency_key="mismatched-read",
                now=111.7,
            )
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM mail_operations"), 1)

    async def test_superseded_operation_handler_never_calls_remote_gateway(self):
        first = await self.service.record_local_intent(
            self.tenant,
            remote_instance_id="rmi_generic",
            kind=OperationKind.MOVE,
            desired_state={"mailbox_id": "mb_g_archive"},
            idempotency_key="superseded-old",
            now=111.8,
        )
        await self.service.record_local_intent(
            self.tenant,
            remote_instance_id="rmi_generic",
            kind=OperationKind.MOVE,
            desired_state={"mailbox_id": "mb_g_trash"},
            idempotency_key="superseded-new",
            now=111.9,
        )
        result = await self.handler.handle(self.tenant, first, now=112.0)
        self.assertEqual(result.outcome, "superseded")
        self.assertEqual(self.gateway.commands, [])

    async def test_repeated_thread_idempotency_reuses_original_group(self):
        first = await self.service.record_thread_intent(
            self.tenant,
            thread_id="thr_one",
            kind=OperationKind.SET_STARRED,
            desired_state={"value": True},
            idempotency_key="thread-star-repeat",
            now=112.1,
        )
        second = await self.service.record_thread_intent(
            self.tenant,
            thread_id="thr_one",
            kind=OperationKind.SET_STARRED,
            desired_state={"value": True},
            idempotency_key="thread-star-repeat",
            now=112.2,
        )
        self.assertEqual(second.operation_group_id, first.operation_group_id)
        self.assertEqual(second.operation_ids, first.operation_ids)
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM mail_operations"), 2)
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM worker_jobs"), 2)

    async def test_same_idempotency_key_reuses_operation_and_job(self):
        first = await self.service.record_local_intent(
            self.tenant,
            remote_instance_id="rmi_generic",
            kind=OperationKind.SET_READ,
            desired_state={"value": True},
            idempotency_key="stable-read",
            now=112,
        )
        second = await self.service.record_local_intent(
            self.tenant,
            remote_instance_id="rmi_generic",
            kind=OperationKind.SET_READ,
            desired_state={"value": True},
            idempotency_key="stable-read",
            now=113,
        )
        self.assertEqual(second, first)
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM mail_operations"), 1)
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM worker_jobs"), 1)

    async def test_thread_operation_keeps_per_message_partial_outcomes(self):
        group = await self.service.record_thread_intent(
            self.tenant,
            thread_id="thr_one",
            kind=OperationKind.SET_READ,
            desired_state={"value": True},
            idempotency_key="thread-read",
            now=114,
        )
        self.assertEqual(len(group.operation_ids), 2)
        records = await self.rows(
            "SELECT id, remote_instance_id FROM mail_operations WHERE operation_group_id = %s ORDER BY remote_instance_id",
            (group.operation_group_id,),
        )
        by_remote = {str(remote): str(operation_id) for operation_id, remote in records}
        self.gateway.states["rmi_generic"] = RemoteOperationState(
            remote_version="v1",
            is_read=False,
            is_starred=False,
            mailbox_native_keys=("INBOX",),
        )
        self.gateway.states["rmi_gmail"] = RemoteOperationState(
            remote_version="g1",
            is_read=False,
            is_starred=False,
            mailbox_native_keys=("\\Inbox", "\\All"),
        )
        self.gateway.results["rmi_gmail"] = RetryableError("temporary network failure")
        first_result = await self.handler.handle(
            self.tenant,
            by_remote["rmi_generic"],
            now=115,
        )
        second_result = await self.handler.handle(
            self.tenant,
            by_remote["rmi_gmail"],
            now=116,
        )
        self.assertEqual(first_result.outcome, "merged")
        self.assertEqual(second_result.outcome, "retry")
        self.assertEqual(
            tuple(row[0] for row in await self.rows(
                "SELECT status FROM mail_operations WHERE operation_group_id = %s ORDER BY remote_instance_id",
                (group.operation_group_id,),
            )),
            ("synced", "retry_wait"),
        )

    async def test_stale_read_recomputes_field_but_stale_move_conflicts(self):
        read_id = await self.service.record_local_intent(
            self.tenant,
            remote_instance_id="rmi_generic",
            kind=OperationKind.SET_READ,
            desired_state={"value": True},
            idempotency_key="stale-read",
            now=117,
        )
        self.gateway.states["rmi_generic"] = RemoteOperationState(
            remote_version="v2",
            is_read=False,
            is_starred=True,
            mailbox_native_keys=("INBOX",),
        )
        read_result = await self.handler.handle(self.tenant, read_id, now=118)
        self.assertEqual(read_result.outcome, "merged")
        self.assertEqual(self.gateway.commands[-1].kind, OperationKind.SET_READ)
        self.assertEqual(self.gateway.commands[-1].expected_remote_version, "v2")
        self.assertTrue(self.gateway.commands[-1].desired_value)

        move_id = await self.service.record_local_intent(
            self.tenant,
            remote_instance_id="rmi_generic",
            kind=OperationKind.MOVE,
            desired_state={"mailbox_id": "mb_g_archive"},
            idempotency_key="stale-move",
            now=119,
        )
        commands_before = len(self.gateway.commands)
        self.gateway.states["rmi_generic"] = RemoteOperationState(
            remote_version="v3",
            is_read=True,
            is_starred=True,
            mailbox_native_keys=("INBOX",),
        )
        move_result = await self.handler.handle(self.tenant, move_id, now=120)
        self.assertEqual(move_result.outcome, "conflict")
        self.assertEqual(len(self.gateway.commands), commands_before)
        self.assertEqual(
            await self.scalar("SELECT status FROM mail_operations WHERE id = %s", (move_id,)),
            "conflict",
        )

    async def test_pending_undo_restores_local_state_and_synced_read_creates_compensation(self):
        pending_id = await self.service.record_local_intent(
            self.tenant,
            remote_instance_id="rmi_generic",
            kind=OperationKind.SET_READ,
            desired_state={"value": True},
            idempotency_key="undo-pending",
            now=121,
        )
        cancelled_id = await self.service.undo(
            self.tenant,
            pending_id,
            idempotency_key="undo-pending-request",
            now=122,
        )
        self.assertEqual(cancelled_id, pending_id)
        self.assertEqual(
            await self.row(
                "SELECT status FROM mail_operations WHERE id = %s",
                (pending_id,),
            ),
            ("cancelled",),
        )
        self.assertEqual(
            await self.scalar("SELECT is_read FROM message_remote_instances WHERE id = 'rmi_generic'"),
            0,
        )

        synced_id = await self.service.record_local_intent(
            self.tenant,
            remote_instance_id="rmi_generic",
            kind=OperationKind.SET_READ,
            desired_state={"value": True},
            idempotency_key="undo-synced",
            now=123,
        )
        self.gateway.states["rmi_generic"] = RemoteOperationState(
            remote_version="v1",
            is_read=False,
            is_starred=False,
            mailbox_native_keys=("INBOX",),
        )
        await self.handler.handle(self.tenant, synced_id, now=124)
        compensation_id = await self.service.undo(
            self.tenant,
            synced_id,
            idempotency_key="undo-synced-request",
            now=125,
        )
        self.assertNotEqual(compensation_id, synced_id)
        desired = json.loads(str(await self.scalar(
            "SELECT desired_state FROM mail_operations WHERE id = %s",
            (compensation_id,),
        )))
        self.assertFalse(desired["value"])

    async def test_permanent_delete_cannot_be_undone_after_sync(self):
        trash_operation_id = await self.service.record_local_intent(
            self.tenant,
            remote_instance_id="rmi_generic",
            kind=OperationKind.TRASH,
            desired_state={},
            idempotency_key="trash-for-delete",
            now=126,
        )
        self.gateway.states["rmi_generic"] = RemoteOperationState(
            remote_version="v1",
            is_read=False,
            is_starred=False,
            mailbox_native_keys=("INBOX",),
        )
        await self.handler.handle(self.tenant, trash_operation_id, now=126.5)
        operation_id = await self.service.record_local_intent(
            self.tenant,
            remote_instance_id="rmi_generic",
            kind=OperationKind.DELETE_PERMANENT,
            desired_state={},
            idempotency_key="permanent-delete",
            confirm_permanent=True,
            now=127,
        )
        self.gateway.states["rmi_generic"] = RemoteOperationState(
            remote_version="v1",
            is_read=False,
            is_starred=False,
            mailbox_native_keys=("Trash",),
        )
        await self.handler.handle(self.tenant, operation_id, now=128)
        with self.assertRaises(ConflictError):
            await self.service.undo(
                self.tenant,
                operation_id,
                idempotency_key="undo-permanent",
                now=129,
            )


class OperationRepositoryStaticTests(MySqlIsolatedAsyncioTestCase):
    async def test_repository_is_sql_only_and_never_commits(self):
        source = __import__("inspect").getsource(OperationRepository)
        self.assertNotIn(".commit(", source)
        self.assertNotIn(".rollback(", source)


if __name__ == "__main__":
    import unittest

    unittest.main()
