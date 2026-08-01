"""Reliable SMTP composition, delivery, verification, and sent-copy contracts."""

from __future__ import annotations

import asyncio
import tempfile
from email import policy
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import AsyncMock, patch

from flymail.domain.enums import ObjectKind
from flymail.domain.errors import ConflictError
from flymail.infrastructure.db.migrations.runner import run_migrations
from flymail.infrastructure.object_store.store import ObjectStore
from flymail.providers.core.smtp_client import (
    ComposedAttachment,
    MimeComposer,
    SendCommand,
    SendRecipient,
    SentAppendResult,
    SentVerificationResult,
    SmtpDeliveryUncertain,
    SmtpSendResult,
)
from flymail.providers.registry import ProviderRegistry
from flymail.repositories.base import TenantContext
from flymail.repositories.jobs import LeasedJob
from flymail.repositories.objects import ObjectRepository
from flymail.repositories.outbox import OutboxRepository
from flymail.workers.dispatcher import JobContext, WorkerDispatcher
from flymail.workers.sender import ReliableSender, SendService
from tests.v2.mysql_test_case import MySqlIsolatedAsyncioTestCase


async def _one_chunk(value: bytes):
    yield value


class FakeMailGateway:
    def __init__(self) -> None:
        self.send_calls = []
        self.append_calls = []
        self.verify_calls = []
        self.send_result: SmtpSendResult | BaseException = SmtpSendResult(250, "accepted")
        self.verify_result = SentVerificationResult(found=False)
        self.append_result = SentAppendResult(remote_uid=77)
        self.before_send = None

    async def send(self, request):
        if self.before_send is not None:
            await self.before_send(request)
        self.send_calls.append(request)
        if isinstance(self.send_result, BaseException):
            raise self.send_result
        return self.send_result

    async def verify_sent(self, request):
        self.verify_calls.append(request)
        return self.verify_result

    async def append_sent_copy(self, request):
        self.append_calls.append(request)
        return self.append_result


class MimeComposerTests(MySqlIsolatedAsyncioTestCase):
    async def test_stable_message_id_bcc_is_envelope_only_and_reply_headers_are_preserved(self):
        command = SendCommand(
            draft_id="drf_compose",
            message_id_header="<stable@example.test>",
            created_at=1_700_000_000,
            from_address="sender@example.com",
            from_display_name="Sender Name",
            reply_to="reply@example.com",
            recipients=(
                SendRecipient("to", "to@example.com", "To User"),
                SendRecipient("cc", "cc@example.com", ""),
                SendRecipient("bcc", "hidden@example.com", "Hidden"),
            ),
            subject="Deterministic message",
            text_body="plain body\n",
            html_body="<p>html body</p>",
            attachments=(
                ComposedAttachment(
                    filename="report.txt",
                    content_type="text/plain",
                    content=b"attachment-body",
                ),
            ),
            in_reply_to="<parent@example.test>",
            references=("<root@example.test>", "<parent@example.test>"),
        )
        first = MimeComposer.compose(command)
        second = MimeComposer.compose(command)
        self.assertEqual(first.source, second.source)
        self.assertEqual(first.message_id_header, "<stable@example.test>")
        self.assertEqual(
            first.envelope_recipients,
            ("to@example.com", "cc@example.com", "hidden@example.com"),
        )
        parsed = BytesParser(policy=policy.default).parsebytes(first.source)
        self.assertEqual(parsed["Message-ID"], "<stable@example.test>")
        self.assertEqual(parsed["In-Reply-To"], "<parent@example.test>")
        self.assertEqual(
            str(parsed["References"]),
            "<root@example.test> <parent@example.test>",
        )
        self.assertIsNone(parsed["Bcc"])
        self.assertEqual(parsed["Reply-To"].addresses[0].addr_spec, "reply@example.com")
        self.assertEqual(len(list(parsed.iter_attachments())), 1)

    async def test_smtp_send_result_rejects_non_success_response(self):
        with self.assertRaises(ValueError):
            SmtpSendResult(421, "temporary failure")


class ReliableSenderTests(MySqlIsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await run_migrations(self.api_pool)
        await self._clear_tables()
        self.temp_dir = tempfile.TemporaryDirectory(prefix="flymail-v2-sender-")
        root = Path(self.temp_dir.name)
        self.store = ObjectStore(root / "objects", root / "tmp")
        self.tenant = TenantContext("usr_sender")
        self.registry = ProviderRegistry.default()
        await self._create_identity_data()
        self.text_object = await self._store_object(
            ObjectKind.BODY_TEXT,
            b"Hello from FlyMail\n",
            "draft_body_text",
            "drf_generic",
        )
        self.attachment_object = await self._store_object(
            ObjectKind.DRAFT_ATTACHMENT,
            b"attachment-payload",
            "draft_attachment",
            "att_generic",
        )
        await self._create_draft(
            draft_id="drf_generic",
            account_id="acc_generic",
            identity_id="ident_generic",
            scheduled_at=None,
        )
        await self._create_draft(
            draft_id="drf_gmail",
            account_id="acc_gmail",
            identity_id="ident_gmail",
            scheduled_at=None,
        )
        await self._create_draft(
            draft_id="drf_scheduled",
            account_id="acc_generic",
            identity_id="ident_generic",
            scheduled_at=500,
        )
        self.gateway = FakeMailGateway()
        self.service = SendService(self.api_pool, self.store, self.registry)
        self.sender = ReliableSender(
            self.worker_pool,
            self.store,
            self.gateway,
            self.registry,
            verification_retry_limit=1,
        )

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()
        await super().asyncTearDown()

    async def _clear_tables(self) -> None:
        tables = (
            "send_attempts",
            "job_attempts",
            "worker_jobs",
            "outbox_events",
            "mail_operations",
            "draft_attachments",
            "draft_recipients",
            "drafts",
            "content_references",
            "content_objects",
            "message_headers",
            "messages",
            "mail_identities",
            "mail_accounts",
            "users",
        )
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                for table in tables:
                    await cursor.execute(f"DELETE FROM {table}")
            await connection.commit()

    async def _create_identity_data(self) -> None:
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO users (
                        id, username, password_hash, role, enabled,
                        password_version, created_at, updated_at
                    ) VALUES ('usr_sender', 'sender', 'test-hash', 'user', 1, 1, 1, 1)
                    """
                )
                await cursor.executemany(
                    """
                    INSERT INTO mail_accounts (
                        id, user_uid, provider_key, email, normalized_email,
                        status, poll_interval_seconds, created_at, updated_at
                    ) VALUES (%s, 'usr_sender', %s, %s, %s, 'active', 300, 1, 1)
                    """,
                    [
                        ("acc_generic", "generic", "sender@example.com", "sender@example.com"),
                        ("acc_gmail", "gmail", "gmail@example.com", "gmail@example.com"),
                    ],
                )
                await cursor.executemany(
                    """
                    INSERT INTO mail_identities (
                        id, user_uid, account_id, from_address,
                        normalized_from_address, display_name, reply_to,
                        signature_html, signature_text, is_default,
                        is_verified, created_at, updated_at
                    ) VALUES (%s, 'usr_sender', %s, %s, %s, %s, %s,
                              NULL, NULL, 1, %s, 1, 1)
                    """,
                    [
                        (
                            "ident_generic", "acc_generic", "sender@example.com",
                            "sender@example.com", "Sender", "reply@example.com", 1,
                        ),
                        (
                            "ident_gmail", "acc_gmail", "gmail@example.com",
                            "gmail@example.com", "Gmail Sender", "", 1,
                        ),
                        (
                            "ident_unverified", "acc_generic", "alias@example.com",
                            "alias@example.com", "Alias", "", 0,
                        ),
                    ],
                )
                await cursor.execute(
                    """
                    INSERT INTO messages (
                        id, user_uid, canonical_message_key, message_id_header,
                        subject, normalized_subject, received_at, created_at, updated_at
                    ) VALUES ('msg_parent', 'usr_sender', 'key:parent',
                              '<parent@example.test>', 'Parent', 'parent', 1, 1, 1)
                    """
                )
                await cursor.execute(
                    """
                    INSERT INTO message_headers (
                        message_id, user_uid, in_reply_to, references_json,
                        list_id, parser_version, parsed_at
                    ) VALUES ('msg_parent', 'usr_sender', '<root@example.test>',
                              '["<root@example.test>"]', '', 1, 1)
                    """
                )
            await connection.commit()

    async def _store_object(
        self,
        kind: ObjectKind,
        value: bytes,
        reference_kind: str,
        reference_id: str,
    ):
        stored = await self.store.put_stream(kind, _one_chunk(value), expected_size=len(value))
        async with self.api_pool.acquire() as connection:
            await connection.begin()
            repository = ObjectRepository(connection)
            async with repository.lock_object(stored.content_sha256):
                await repository.attach_reference(
                    stored,
                    user_uid=self.tenant.user_uid,
                    reference_kind=reference_kind,
                    reference_id=reference_id,
                    pinned=True,
                    last_accessed_at=1,
                )
            await connection.commit()
        return stored

    async def _create_draft(
        self,
        *,
        draft_id: str,
        account_id: str,
        identity_id: str,
        scheduled_at: float | None,
    ) -> None:
        body_hash = self.text_object.content_sha256
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO drafts (
                        id, user_uid, account_id, identity_id, thread_id,
                        reply_to_message_id, subject, body_text_object_sha256,
                        version, status, scheduled_at, send_message_id,
                        created_at, updated_at
                    ) VALUES (%s, 'usr_sender', %s, %s, 'thr_reply',
                              'msg_parent', 'Reliable send', %s, 1, 'draft', %s, '', 10, 10)
                    """,
                    (draft_id, account_id, identity_id, body_hash, scheduled_at),
                )
                await cursor.executemany(
                    """
                    INSERT INTO draft_recipients (
                        id, draft_id, user_uid, recipient_kind,
                        address, normalized_address, display_name, position_index
                    ) VALUES (%s, %s, 'usr_sender', %s, %s, %s, %s, %s)
                    """,
                    [
                        (f"rcp_{draft_id}_to", draft_id, "to", "to@example.com", "to@example.com", "To", 0),
                        (f"rcp_{draft_id}_cc", draft_id, "cc", "cc@example.com", "cc@example.com", "", 0),
                        (f"rcp_{draft_id}_bcc", draft_id, "bcc", "hidden@example.com", "hidden@example.com", "", 0),
                    ],
                )
                if draft_id == "drf_generic":
                    await cursor.execute(
                        """
                        INSERT INTO draft_attachments (
                            id, draft_id, user_uid, content_sha256,
                            filename, content_type, size_bytes,
                            position_index, created_at
                        ) VALUES ('att_generic', %s, 'usr_sender', %s,
                                  'report.bin', 'application/octet-stream', %s, 0, 1)
                        """,
                        (
                            draft_id,
                            self.attachment_object.content_sha256,
                            self.attachment_object.original_size_bytes,
                        ),
                    )
            await connection.commit()

    def context(self, *, account_id: str, provider_key: str, attempt: int = 1) -> JobContext:
        return JobContext(
            job_id=f"job_{account_id}_{attempt}",
            user_uid=self.tenant.user_uid,
            account_id=account_id,
            provider_key=provider_key,
            queue_name="send",
            worker_id="worker_sender",
            attempt_count=attempt,
            stop_event=asyncio.Event(),
        )

    async def scalar(self, sql: str, params: tuple | list = ()):
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(sql, params)
                row = await cursor.fetchone()
                return row[0] if row else None

    async def row(self, sql: str, params: tuple | list = ()):
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(sql, params)
                return await cursor.fetchone()

    async def rows(self, sql: str, params: tuple | list = ()):
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(sql, params)
                return await cursor.fetchall()

    async def test_concurrent_same_queue_key_creates_one_operation_job_and_outbox(self):
        results = await asyncio.gather(
            *(
                self.service.queue_draft(
                    self.tenant,
                    "drf_scheduled",
                    idempotency_key="concurrent-queue",
                    now=99,
                )
                for _ in range(8)
            )
        )
        self.assertEqual(len({result.operation_id for result in results}), 1)
        self.assertEqual(len({result.job_id for result in results}), 1)
        self.assertEqual(len({result.message_id_header for result in results}), 1)
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM mail_operations"), 1)
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM worker_jobs"), 1)
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM outbox_events"), 1)

    async def test_queue_is_atomic_stable_and_scheduled_job_uses_available_at(self):
        first = await self.service.queue_draft(
            self.tenant,
            "drf_scheduled",
            idempotency_key="queue-scheduled",
            now=100,
        )
        second = await self.service.queue_draft(
            self.tenant,
            "drf_scheduled",
            idempotency_key="queue-scheduled",
            now=101,
        )
        self.assertEqual(second, first)
        self.assertTrue(first.message_id_header.startswith("<"))
        self.assertEqual(
            await self.row(
                "SELECT status, send_state, scheduled_at, send_message_id FROM drafts WHERE id='drf_scheduled'"
            ),
            ("queued", "queued", 500.0, first.message_id_header),
        )
        self.assertEqual(
            await self.row(
                "SELECT job_kind, available_at FROM worker_jobs WHERE id=%s",
                (first.job_id,),
            ),
            ("send.deliver", 500.0),
        )
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM mail_operations"), 1)
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM outbox_events"), 1)

    async def test_unverified_or_cross_account_identity_is_rejected(self):
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "UPDATE drafts SET identity_id='ident_unverified' WHERE id='drf_generic'"
                )
            await connection.commit()
        with self.assertRaises(ConflictError):
            await self.service.queue_draft(
                self.tenant,
                "drf_generic",
                idempotency_key="unverified",
                now=110,
            )
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "UPDATE drafts SET identity_id='ident_gmail' WHERE id='drf_generic'"
                )
            await connection.commit()
        with self.assertRaises(ConflictError):
            await self.service.queue_draft(
                self.tenant,
                "drf_generic",
                idempotency_key="cross-account",
                now=111,
            )

    async def test_queue_rolls_back_draft_operation_job_and_outbox_together(self):
        with patch.object(
            OutboxRepository,
            "append",
            new=AsyncMock(side_effect=RuntimeError("outbox unavailable")),
        ):
            with self.assertRaises(RuntimeError):
                await self.service.queue_draft(
                    self.tenant,
                    "drf_generic",
                    idempotency_key="queue-rollback",
                    now=115,
                )
        self.assertEqual(
            await self.row(
                "SELECT status, send_state, send_message_id FROM drafts WHERE id='drf_generic'"
            ),
            ("draft", "draft", ""),
        )
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM mail_operations"), 0)
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM worker_jobs"), 0)
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM outbox_events"), 0)

    async def test_queue_rejects_invalid_reply_to_before_persisting_send(self):
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "UPDATE mail_identities SET reply_to='not-an-email' WHERE id='ident_generic'"
                )
            await connection.commit()
        with self.assertRaises(ValueError):
            await self.service.queue_draft(
                self.tenant,
                "drf_generic",
                idempotency_key="invalid-reply-to",
                now=116,
            )
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM mail_operations"), 0)
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM worker_jobs"), 0)

    async def test_attempt_is_persisted_before_network_and_accepted_send_marks_sent_once(self):
        queued = await self.service.queue_draft(
            self.tenant,
            "drf_generic",
            idempotency_key="accepted-send",
            now=120,
        )

        async def assert_attempt_exists(_request):
            self.assertEqual(
                await self.row(
                    "SELECT status, attempt_number FROM send_attempts WHERE operation_id=%s",
                    (queued.operation_id,),
                ),
                ("sending", 1),
            )

        self.gateway.before_send = assert_attempt_exists
        result = await self.sender.deliver(
            self.context(account_id="acc_generic", provider_key="generic"),
            {"draft_id": "drf_generic", "operation_id": queued.operation_id},
        )
        self.assertEqual(result.action, "complete")
        self.assertEqual(len(self.gateway.send_calls), 1)
        request = self.gateway.send_calls[0]
        parsed = BytesParser(policy=policy.default).parsebytes(request.source)
        self.assertEqual(parsed["Message-ID"], queued.message_id_header)
        self.assertIsNone(parsed["Bcc"])
        self.assertIn("hidden@example.com", request.envelope_recipients)
        sent_row = await self.row(
            "SELECT status, send_state, sent_at FROM drafts WHERE id='drf_generic'"
        )
        self.assertEqual(sent_row[:2], ("sent", "sent"))
        self.assertIsNotNone(sent_row[2])
        self.assertEqual(
            await self.row(
                "SELECT status, smtp_response_code FROM send_attempts WHERE operation_id=%s",
                (queued.operation_id,),
            ),
            ("sent", 250),
        )
        repeated = await self.sender.deliver(
            self.context(account_id="acc_generic", provider_key="generic", attempt=2),
            {"draft_id": "drf_generic", "operation_id": queued.operation_id},
        )
        self.assertEqual(repeated.action, "complete")
        self.assertEqual(len(self.gateway.send_calls), 1)

    async def test_delivery_updates_exact_operation_not_newer_operation_for_same_draft(self):
        queued = await self.service.queue_draft(
            self.tenant,
            "drf_generic",
            idempotency_key="exact-operation",
            now=125,
        )
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO mail_operations (
                        id, user_uid, operation_type, target_type, target_id,
                        account_id, desired_state, status, priority,
                        available_at, attempt_count, idempotency_key,
                        created_at, updated_at
                    ) VALUES (
                        'op_newer_same_draft', 'usr_sender', 'send', 'draft',
                        'drf_generic', 'acc_generic', '{}', 'pending', 10,
                        126, 0, 'newer-same-draft', 126, 126
                    )
                    """
                )
            await connection.commit()
        outcome = await self.sender.deliver(
            self.context(account_id="acc_generic", provider_key="generic"),
            {"draft_id": "drf_generic", "operation_id": queued.operation_id},
        )
        self.assertEqual(outcome.action, "complete")
        self.assertEqual(
            await self.scalar(
                "SELECT status FROM mail_operations WHERE id=%s",
                (queued.operation_id,),
            ),
            "synced",
        )
        self.assertEqual(
            await self.scalar(
                "SELECT status FROM mail_operations WHERE id='op_newer_same_draft'"
            ),
            "pending",
        )

    async def test_stale_sending_retry_schedules_verification_without_resend(self):
        queued = await self.service.queue_draft(
            self.tenant,
            "drf_generic",
            idempotency_key="stale-sending",
            now=128,
        )
        with patch.object(
            self.sender,
            "_load_and_compose",
            new=AsyncMock(side_effect=RuntimeError("worker stopped")),
        ):
            with self.assertRaises(RuntimeError):
                await self.sender.deliver(
                    self.context(account_id="acc_generic", provider_key="generic"),
                    {"draft_id": "drf_generic", "operation_id": queued.operation_id},
                )
        recovered = await self.sender.deliver(
            self.context(account_id="acc_generic", provider_key="generic", attempt=2),
            {"draft_id": "drf_generic", "operation_id": queued.operation_id},
        )
        self.assertEqual(recovered.action, "complete")
        self.assertEqual(self.gateway.send_calls, [])
        self.assertEqual(
            await self.row(
                "SELECT status, send_state FROM drafts WHERE id='drf_generic'"
            ),
            ("review_required", "verification_required"),
        )
        self.assertEqual(
            await self.scalar(
                "SELECT status FROM send_attempts WHERE operation_id=%s",
                (queued.operation_id,),
            ),
            "verification_required",
        )
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM worker_jobs WHERE job_kind='send.verify'"
            ),
            1,
        )

    async def test_disconnect_after_data_enters_verification_without_direct_resend(self):
        queued = await self.service.queue_draft(
            self.tenant,
            "drf_generic",
            idempotency_key="uncertain-send",
            now=130,
        )
        self.gateway.send_result = SmtpDeliveryUncertain("connection lost after DATA")
        outcome = await self.sender.deliver(
            self.context(account_id="acc_generic", provider_key="generic"),
            {"draft_id": "drf_generic", "operation_id": queued.operation_id},
        )
        self.assertEqual(outcome.action, "complete")
        self.assertEqual(
            await self.row(
                "SELECT status, send_state FROM drafts WHERE id='drf_generic'"
            ),
            ("review_required", "verification_required"),
        )
        self.assertEqual(
            await self.scalar(
                "SELECT status FROM send_attempts WHERE operation_id=%s",
                (queued.operation_id,),
            ),
            "verification_required",
        )
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM worker_jobs WHERE job_kind='send.verify'"
            ),
            1,
        )
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM worker_jobs WHERE job_kind='send.deliver'"
            ),
            1,
        )

    async def test_verify_job_scope_must_match_persisted_account_and_provider(self):
        queued = await self.service.queue_draft(
            self.tenant,
            "drf_generic",
            idempotency_key="verify-wrong-scope",
            now=135,
        )
        self.gateway.send_result = SmtpDeliveryUncertain("uncertain")
        await self.sender.deliver(
            self.context(account_id="acc_generic", provider_key="generic"),
            {"draft_id": "drf_generic", "operation_id": queued.operation_id},
        )
        with self.assertRaises(ConflictError):
            await self.sender.verify(
                self.context(account_id="acc_gmail", provider_key="gmail"),
                {"draft_id": "drf_generic", "operation_id": queued.operation_id},
            )
        self.assertEqual(self.gateway.verify_calls, [])

    async def test_local_compose_infrastructure_failure_is_not_smtp_retry(self):
        queued = await self.service.queue_draft(
            self.tenant,
            "drf_generic",
            idempotency_key="compose-infrastructure",
            now=136,
        )
        with patch.object(
            self.sender,
            "_load_and_compose",
            new=AsyncMock(side_effect=RuntimeError("database unavailable")),
        ):
            with self.assertRaises(RuntimeError):
                await self.sender.deliver(
                    self.context(account_id="acc_generic", provider_key="generic"),
                    {"draft_id": "drf_generic", "operation_id": queued.operation_id},
                )
        self.assertEqual(self.gateway.send_calls, [])
        self.assertEqual(
            await self.row(
                "SELECT status, send_state FROM drafts WHERE id='drf_generic'"
            ),
            ("sending", "sending"),
        )
        self.assertEqual(
            await self.scalar(
                "SELECT status FROM mail_operations WHERE id=%s",
                (queued.operation_id,),
            ),
            "applying",
        )
        self.assertEqual(
            await self.scalar(
                "SELECT status FROM send_attempts WHERE operation_id=%s",
                (queued.operation_id,),
            ),
            "sending",
        )

    async def test_verification_found_marks_sent_without_resend(self):
        queued = await self.service.queue_draft(
            self.tenant,
            "drf_generic",
            idempotency_key="verify-found",
            now=140,
        )
        self.gateway.send_result = SmtpDeliveryUncertain("uncertain")
        await self.sender.deliver(
            self.context(account_id="acc_generic", provider_key="generic"),
            {"draft_id": "drf_generic", "operation_id": queued.operation_id},
        )
        self.gateway.verify_result = SentVerificationResult(
            found=True,
            remote_uid=88,
            provider_message_id="provider-sent-id",
        )
        outcome = await self.sender.verify(
            self.context(account_id="acc_generic", provider_key="generic"),
            {"draft_id": "drf_generic", "operation_id": queued.operation_id},
        )
        self.assertEqual(outcome.action, "complete")
        self.assertEqual(len(self.gateway.send_calls), 1)
        self.assertEqual(
            await self.row("SELECT status, send_state FROM drafts WHERE id='drf_generic'"),
            ("sent", "sent"),
        )

    async def test_verification_not_found_allows_one_controlled_retry_then_review(self):
        queued = await self.service.queue_draft(
            self.tenant,
            "drf_generic",
            idempotency_key="verify-missing",
            now=150,
        )
        self.gateway.send_result = SmtpDeliveryUncertain("uncertain")
        await self.sender.deliver(
            self.context(account_id="acc_generic", provider_key="generic"),
            {"draft_id": "drf_generic", "operation_id": queued.operation_id},
        )
        self.gateway.verify_result = SentVerificationResult(found=False)
        first = await self.sender.verify(
            self.context(account_id="acc_generic", provider_key="generic"),
            {"draft_id": "drf_generic", "operation_id": queued.operation_id},
        )
        self.assertEqual(first.action, "complete")
        self.assertEqual(
            await self.row(
                "SELECT send_state, verification_attempts FROM drafts WHERE id='drf_generic'"
            ),
            ("queued", 1),
        )
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM worker_jobs WHERE job_kind='send.deliver'"
            ),
            2,
        )
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "UPDATE drafts SET status='review_required', send_state='verification_required' WHERE id='drf_generic'"
                )
            await connection.commit()
        second = await self.sender.verify(
            self.context(account_id="acc_generic", provider_key="generic", attempt=2),
            {"draft_id": "drf_generic", "operation_id": queued.operation_id},
        )
        self.assertEqual(second.action, "complete")
        self.assertEqual(
            await self.row(
                "SELECT status, send_state, verification_attempts FROM drafts WHERE id='drf_generic'"
            ),
            ("review_required", "review_required", 2),
        )
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM worker_jobs WHERE job_kind='send.deliver'"
            ),
            2,
        )

    async def test_controlled_retry_reuses_exact_composed_source(self):
        queued = await self.service.queue_draft(
            self.tenant,
            "drf_generic",
            idempotency_key="exact-source-retry",
            now=155,
        )
        self.gateway.send_result = SmtpDeliveryUncertain("uncertain")
        await self.sender.deliver(
            self.context(account_id="acc_generic", provider_key="generic"),
            {"draft_id": "drf_generic", "operation_id": queued.operation_id},
        )
        first_source = self.gateway.send_calls[0].source
        self.gateway.verify_result = SentVerificationResult(found=False)
        await self.sender.verify(
            self.context(account_id="acc_generic", provider_key="generic"),
            {"draft_id": "drf_generic", "operation_id": queued.operation_id},
        )
        self.gateway.send_result = SmtpSendResult(250, "accepted")
        outcome = await self.sender.deliver(
            self.context(account_id="acc_generic", provider_key="generic", attempt=2),
            {"draft_id": "drf_generic", "operation_id": queued.operation_id},
        )
        self.assertEqual(outcome.action, "complete")
        self.assertEqual(len(self.gateway.send_calls), 2)
        self.assertEqual(self.gateway.send_calls[1].source, first_source)
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM content_references WHERE reference_kind='raw_eml' AND reference_id='drf_generic'"
            ),
            1,
        )

    async def test_smtp_utf8_requirement_fails_before_gateway_when_provider_lacks_support(self):
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    UPDATE draft_recipients
                    SET address='收件人@example.com', normalized_address='收件人@example.com'
                    WHERE draft_id='drf_generic' AND recipient_kind='to'
                    """
                )
            await connection.commit()
        queued = await self.service.queue_draft(
            self.tenant,
            "drf_generic",
            idempotency_key="smtp-utf8-unsupported",
            now=156,
        )
        outcome = await self.sender.deliver(
            self.context(account_id="acc_generic", provider_key="generic"),
            {"draft_id": "drf_generic", "operation_id": queued.operation_id},
        )
        self.assertEqual(outcome.action, "fail")
        self.assertEqual(self.gateway.send_calls, [])
        self.assertEqual(
            await self.row("SELECT status, send_state FROM drafts WHERE id='drf_generic'"),
            ("failed", "failed"),
        )

    async def test_job_context_must_match_persisted_account_and_provider(self):
        queued = await self.service.queue_draft(
            self.tenant,
            "drf_generic",
            idempotency_key="wrong-job-scope",
            now=157,
        )
        with self.assertRaises(ConflictError):
            await self.sender.deliver(
                self.context(account_id="acc_gmail", provider_key="gmail"),
                {"draft_id": "drf_generic", "operation_id": queued.operation_id},
            )
        self.assertEqual(self.gateway.send_calls, [])
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM send_attempts"), 0)

    async def test_deliver_handler_registers_directly_with_worker_dispatcher(self):
        queued = await self.service.queue_draft(
            self.tenant,
            "drf_generic",
            idempotency_key="dispatcher-send",
            now=158,
        )
        dispatcher = WorkerDispatcher()
        dispatcher.register("send.deliver", self.sender.handle)
        outcome = await dispatcher.dispatch(
            LeasedJob(
                id=queued.job_id,
                user_uid=self.tenant.user_uid,
                account_id="acc_generic",
                provider_key="generic",
                queue_name="send",
                job_kind="send.deliver",
                priority=10,
                available_at=158,
                lease_owner="worker_sender",
                lease_token="lease_sender_dispatch",
                lease_expires_at=218,
                attempt_count=1,
                max_attempts=3,
                dedupe_key=f"send-deliver:{queued.operation_id}:0",
                payload={
                    "draft_id": "drf_generic",
                    "operation_id": queued.operation_id,
                },
            ),
            stop_event=asyncio.Event(),
        )
        self.assertEqual(outcome.action, "complete")
        self.assertEqual(len(self.gateway.send_calls), 1)

    async def test_provider_auto_skips_append_and_generic_creates_idempotent_append(self):
        generic = await self.service.queue_draft(
            self.tenant,
            "drf_generic",
            idempotency_key="generic-copy",
            now=160,
        )
        await self.sender.deliver(
            self.context(account_id="acc_generic", provider_key="generic"),
            {"draft_id": "drf_generic", "operation_id": generic.operation_id},
        )
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM worker_jobs WHERE job_kind='send.append_sent_copy'"
            ),
            1,
        )
        append_outcome = await self.sender.append_sent_copy(
            self.context(account_id="acc_generic", provider_key="generic"),
            {"draft_id": "drf_generic", "operation_id": generic.operation_id},
        )
        self.assertEqual(append_outcome.action, "complete")
        self.assertEqual(len(self.gateway.append_calls), 1)
        self.assertEqual(
            await self.scalar(
                """
                SELECT JSON_UNQUOTE(JSON_EXTRACT(payload, '$.event.remote_uid'))
                FROM outbox_events
                WHERE event_type='mail.sent_copy.appended'
                """
            ),
            "77",
        )
        repeated = await self.sender.append_sent_copy(
            self.context(account_id="acc_generic", provider_key="generic", attempt=2),
            {"draft_id": "drf_generic", "operation_id": generic.operation_id},
        )
        self.assertEqual(repeated.action, "complete")
        self.assertEqual(len(self.gateway.append_calls), 1)

        gmail = await self.service.queue_draft(
            self.tenant,
            "drf_gmail",
            idempotency_key="gmail-copy",
            now=161,
        )
        await self.sender.deliver(
            self.context(account_id="acc_gmail", provider_key="gmail"),
            {"draft_id": "drf_gmail", "operation_id": gmail.operation_id},
        )
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM worker_jobs WHERE job_kind='send.append_sent_copy'"
            ),
            1,
        )

    async def test_append_database_failure_never_repeats_remote_append(self):
        queued = await self.service.queue_draft(
            self.tenant,
            "drf_generic",
            idempotency_key="append-ambiguity",
            now=165,
        )
        await self.sender.deliver(
            self.context(account_id="acc_generic", provider_key="generic"),
            {"draft_id": "drf_generic", "operation_id": queued.operation_id},
        )
        original_append = OutboxRepository.append

        async def fail_after_remote(repository, event_type, *args, **kwargs):
            if event_type == "mail.sent_copy.appended":
                raise RuntimeError("database unavailable after APPEND")
            return await original_append(repository, event_type, *args, **kwargs)

        with patch.object(OutboxRepository, "append", new=fail_after_remote):
            with self.assertRaises(RuntimeError):
                await self.sender.append_sent_copy(
                    self.context(account_id="acc_generic", provider_key="generic"),
                    {"draft_id": "drf_generic", "operation_id": queued.operation_id},
                )
        self.assertEqual(len(self.gateway.append_calls), 1)
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM outbox_events WHERE event_type='mail.sent_copy.append_started'"
            ),
            1,
        )
        retry = await self.sender.append_sent_copy(
            self.context(account_id="acc_generic", provider_key="generic", attempt=2),
            {"draft_id": "drf_generic", "operation_id": queued.operation_id},
        )
        self.assertEqual(retry.action, "fail")
        self.assertEqual(retry.error_class, "SentCopyResultUncertain")
        self.assertEqual(len(self.gateway.append_calls), 1)

    async def test_queued_send_can_be_cancelled_before_delivery(self):
        queued = await self.service.queue_draft(
            self.tenant,
            "drf_generic",
            idempotency_key="cancel-send",
            now=170,
        )
        await self.service.cancel(
            self.tenant,
            "drf_generic",
            operation_id=queued.operation_id,
            now=171,
        )
        self.assertEqual(
            await self.row("SELECT status, send_state FROM drafts WHERE id='drf_generic'"),
            ("cancelled", "cancelled"),
        )
        self.assertEqual(
            await self.scalar("SELECT status FROM mail_operations WHERE id=%s", (queued.operation_id,)),
            "cancelled",
        )
        self.assertEqual(
            await self.scalar("SELECT status FROM worker_jobs WHERE id=%s", (queued.job_id,)),
            "cancelled",
        )
        outcome = await self.sender.deliver(
            self.context(account_id="acc_generic", provider_key="generic"),
            {"draft_id": "drf_generic", "operation_id": queued.operation_id},
        )
        self.assertEqual(outcome.action, "complete")
        self.assertEqual(self.gateway.send_calls, [])


if __name__ == "__main__":
    import unittest

    unittest.main()
