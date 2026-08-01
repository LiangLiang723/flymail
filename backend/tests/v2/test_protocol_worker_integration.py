"""Gate 2 protocol and Worker integration contracts."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from flymail.domain.enums import ObjectKind
from flymail.domain.errors import ConfigurationError
from flymail.domain.operations import OperationKind, RemoteOperationState
from flymail.infrastructure.db.migrations.runner import run_migrations
from flymail.infrastructure.object_store.store import ObjectStore
from flymail.infrastructure.security.credentials import CredentialCipher
from flymail.notifications.channels import ChannelRegistry
from flymail.notifications.contracts import HttpResponse
from flymail.notifications.image_publishers import ImagePublisherRegistry
from flymail.providers.core.imap_commands import IdleEvent
from flymail.providers.core.rate_limit import AccountConnectionLimiter
from flymail.providers.core.smtp_client import SentVerificationResult, SmtpDeliveryUncertain
from flymail.providers.registry import ProviderRegistry
from flymail.repositories.accounts import AccountRepository, IdentityRepository
from flymail.repositories.base import AdminContext, TenantContext
from flymail.repositories.jobs import JobRepository, JobSpec, LeasedJob
from flymail.repositories.mailboxes import MailboxRepository
from flymail.repositories.objects import ObjectRepository
from flymail.repositories.users import UserRepository
from flymail.workers.content_fetch import ContentFetchService, ContentJobPublisher
from flymail.workers.dispatcher import JobOutcome, WorkerDispatcher
from flymail.workers.idle import IdleAccountSnapshot, IdleSupervisor
from flymail.workers.ingestion import MessageIngestionService, RemoteSummary
from flymail.workers.notifications import NotificationDeliveryHandler, NotificationService
from flymail.workers.operation_apply import OperationApplyHandler, OperationService
from flymail.workers.sender import ReliableSender, SendService
from flymail.workers.scheduler import FairScheduler
from tests.v2 import test_content_fetch as content_fetch_fixtures
from tests.v2.mysql_test_case import MySqlIsolatedAsyncioTestCase
from tests.v2.test_content_fetch import FakeContentTransport
from tests.v2.test_idle_reconciliation import (
    FakeAccountSource,
    FakeIdleSession,
    FakePublisher,
    FakeSessionFactory,
)
from tests.v2.test_notification_dispatch import FakeHttpTransport
from tests.v2.test_operation_apply import FakeOperationGateway
from tests.v2.test_reliable_sender import FakeMailGateway
from v2_worker import (
    WORKER_JOB_KINDS,
    build_worker_dispatcher,
    run_worker,
    validate_worker_job_registry,
)


EXPECTED_JOB_KINDS = (
    "content.attachment",
    "content.body",
    "content.inline",
    "content.raw_eml",
    "mail.operation.apply",
    "notification.deliver",
    "send.append_sent_copy",
    "send.deliver",
    "send.verify",
    "sync.incremental",
    "sync.initial",
    "sync.mailbox_refresh",
    "sync.reconcile",
)


async def _success_handler(_context, _payload):
    return JobOutcome.success()


async def _one_chunk(value: bytes):
    yield value


def _decode_b64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class WorkerRegistryIntegrationTests(MySqlIsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await run_migrations(self.pool)
        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("DELETE FROM job_attempts")
                await cursor.execute("DELETE FROM worker_jobs")
                await cursor.execute("DELETE FROM account_runtime_state")
                await cursor.execute("DELETE FROM mail_accounts")
                await cursor.execute("DELETE FROM users")
            await connection.commit()

    def complete_handlers(self, **overrides):
        handlers = {kind: _success_handler for kind in EXPECTED_JOB_KINDS}
        handlers.update(overrides)
        return handlers

    async def _create_worker_account(self, account_id: str, provider_key: str) -> None:
        user_uid = f"usr_{account_id}"
        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO users (
                        id, username, password_hash, role, enabled,
                        password_version, created_at, updated_at
                    ) VALUES (%s, %s, 'test-hash', 'user', 1, 1, 1, 1)
                    """,
                    (user_uid, user_uid),
                )
                await cursor.execute(
                    """
                    INSERT INTO mail_accounts (
                        id, user_uid, provider_key, email, normalized_email,
                        status, poll_interval_seconds, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, 'active', 300, 1, 1)
                    """,
                    (
                        account_id,
                        user_uid,
                        provider_key,
                        f"{account_id}@example.test",
                        f"{account_id}@example.test",
                    ),
                )
            await connection.commit()

    async def _enqueue(
        self,
        kind: str,
        *,
        queue_name: str = "interactive",
        user_uid: str | None = None,
        account_id: str | None = None,
        provider_key: str | None = None,
        dedupe_suffix: str = "",
    ) -> str:
        async with self.pool.acquire() as connection:
            await connection.begin()
            job_id = await JobRepository(connection).enqueue(
                JobSpec(
                    queue_name=queue_name,
                    job_kind=kind,
                    payload={"probe_id": "gate2"},
                    user_uid=user_uid,
                    account_id=account_id,
                    provider_key=provider_key,
                    priority=1,
                    available_at=1,
                    max_attempts=3,
                    dedupe_key=f"gate2:{kind}:{dedupe_suffix}",
                ),
                now=1,
            )
            await connection.commit()
            return job_id

    async def scalar(self, sql: str, params: tuple | list = ()):
        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(sql, params)
                row = await cursor.fetchone()
                return row[0] if row else None

    async def test_current_schema_declares_exact_worker_job_kinds(self):
        self.assertEqual(WORKER_JOB_KINDS, EXPECTED_JOB_KINDS)

    async def test_dispatcher_builder_requires_complete_exact_mapping(self):
        handlers = {kind: _success_handler for kind in EXPECTED_JOB_KINDS}
        dispatcher = build_worker_dispatcher(handlers)
        self.assertEqual(dispatcher.registered_kinds, EXPECTED_JOB_KINDS)

        incomplete = dict(handlers)
        incomplete.pop("send.verify")
        with self.assertRaises(ConfigurationError):
            build_worker_dispatcher(incomplete)

        unexpected = dict(handlers)
        unexpected["unknown.future.job"] = _success_handler
        with self.assertRaises(ConfigurationError):
            build_worker_dispatcher(unexpected)

    async def test_startup_gate_rejects_unregistered_runnable_job_before_claim(self):
        job_id = await self._enqueue("content.body")
        dispatcher = WorkerDispatcher()
        with self.assertRaises(ConfigurationError):
            await validate_worker_job_registry(self.pool, dispatcher)
        self.assertEqual(
            await self.scalar(
                "SELECT status FROM worker_jobs WHERE id = %s",
                (job_id,),
            ),
            "pending",
        )

    async def test_startup_gate_ignores_terminal_jobs_and_accepts_registered_kind(self):
        job_id = await self._enqueue("content.body")
        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "UPDATE worker_jobs SET status='succeeded', finished_at=2 WHERE id=%s",
                    (job_id,),
                )
            await connection.commit()
        await validate_worker_job_registry(self.pool, WorkerDispatcher())

        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "UPDATE worker_jobs SET status='pending', finished_at=NULL WHERE id=%s",
                    (job_id,),
                )
            await connection.commit()
        dispatcher = WorkerDispatcher()
        dispatcher.register("content.body", _success_handler)
        await validate_worker_job_registry(self.pool, dispatcher)

    async def test_slow_failed_account_does_not_block_another_account(self):
        await self._create_worker_account("acc_gate2_a", "generic")
        await self._create_worker_account("acc_gate2_b", "gmail")
        job_a = await self._enqueue(
            "sync.incremental",
            queue_name="realtime",
            user_uid="usr_acc_gate2_a",
            account_id="acc_gate2_a",
            provider_key="generic",
            dedupe_suffix="a",
        )
        job_b = await self._enqueue(
            "sync.incremental",
            queue_name="realtime",
            user_uid="usr_acc_gate2_b",
            account_id="acc_gate2_b",
            provider_key="gmail",
            dedupe_suffix="b",
        )
        a_started = asyncio.Event()
        b_completed = asyncio.Event()
        release_a = asyncio.Event()
        a_finished = asyncio.Event()
        stop = asyncio.Event()

        async def sync_handler(context, _payload):
            if context.account_id == "acc_gate2_a":
                a_started.set()
                await release_a.wait()
                a_finished.set()
                return JobOutcome.retry(
                    "ProviderRateLimited",
                    "account A will retry",
                    base_seconds=1,
                    max_seconds=1,
                )
            b_completed.set()
            return JobOutcome.success()

        dispatcher = build_worker_dispatcher(
            self.complete_handlers(**{"sync.incremental": sync_handler})
        )
        env = {
            "DATABASE_URL": self.database_url(),
            "FLYMAIL_DATA_DIR": "/tmp/flymail-v2-gate2-isolation",
            "FLYMAIL_SESSION_SECRET": "gate2-worker-session-secret",
        }
        with patch.dict(os.environ, env, clear=False):
            task = asyncio.create_task(
                run_worker(
                    stop_event=stop,
                    dispatcher=dispatcher,
                    scheduler=FairScheduler(global_slots=2, per_account_limit=1),
                    poll_seconds=0.01,
                    shutdown_grace_seconds=1,
                )
            )
            await asyncio.wait_for(a_started.wait(), timeout=2)
            await asyncio.wait_for(b_completed.wait(), timeout=2)
            self.assertFalse(release_a.is_set())
            release_a.set()
            await asyncio.wait_for(a_finished.wait(), timeout=2)
            for _ in range(100):
                states = await self.scalar(
                    """
                    SELECT COUNT(*) FROM worker_jobs
                    WHERE (id=%s AND status='retry_wait')
                       OR (id=%s AND status='succeeded')
                    """,
                    (job_a, job_b),
                )
                if int(states or 0) == 2:
                    break
                await asyncio.sleep(0.01)
            stop.set()
            await asyncio.wait_for(task, timeout=3)
        self.assertEqual(
            await self.scalar("SELECT status FROM worker_jobs WHERE id=%s", (job_a,)),
            "retry_wait",
        )
        self.assertEqual(
            await self.scalar("SELECT status FROM worker_jobs WHERE id=%s", (job_b,)),
            "succeeded",
        )

    async def test_worker_restart_resumes_released_job_without_duplicate_completion(self):
        job_id = await self._enqueue(
            "notification.deliver",
            queue_name="realtime",
            dedupe_suffix="restart",
        )
        started = asyncio.Event()
        first_stop = asyncio.Event()

        async def interrupted_handler(_context, _payload):
            started.set()
            await asyncio.Event().wait()
            return JobOutcome.success()

        first_dispatcher = build_worker_dispatcher(
            self.complete_handlers(**{"notification.deliver": interrupted_handler})
        )
        env = {
            "DATABASE_URL": self.database_url(),
            "FLYMAIL_DATA_DIR": "/tmp/flymail-v2-gate2-restart",
            "FLYMAIL_SESSION_SECRET": "gate2-worker-session-secret",
        }
        with patch.dict(os.environ, env, clear=False):
            first = asyncio.create_task(
                run_worker(
                    stop_event=first_stop,
                    dispatcher=first_dispatcher,
                    scheduler=FairScheduler(global_slots=1),
                    poll_seconds=0.01,
                    shutdown_grace_seconds=0.01,
                )
            )
            await asyncio.wait_for(started.wait(), timeout=2)
            first_stop.set()
            await asyncio.wait_for(first, timeout=3)
        self.assertEqual(
            await self.scalar("SELECT status FROM worker_jobs WHERE id=%s", (job_id,)),
            "retry_wait",
        )

        completed = asyncio.Event()
        second_stop = asyncio.Event()

        async def resumed_handler(_context, _payload):
            completed.set()
            second_stop.set()
            return JobOutcome.success()

        second_dispatcher = build_worker_dispatcher(
            self.complete_handlers(**{"notification.deliver": resumed_handler})
        )
        with patch.dict(os.environ, env, clear=False):
            await asyncio.wait_for(
                run_worker(
                    stop_event=second_stop,
                    dispatcher=second_dispatcher,
                    scheduler=FairScheduler(global_slots=1),
                    poll_seconds=0.01,
                    shutdown_grace_seconds=1,
                ),
                timeout=3,
            )
        self.assertTrue(completed.is_set())
        self.assertEqual(
            await self.scalar("SELECT status FROM worker_jobs WHERE id=%s", (job_id,)),
            "succeeded",
        )
        self.assertEqual(
            await self.scalar("SELECT COUNT(*) FROM job_attempts WHERE job_id=%s", (job_id,)),
            2,
        )

    async def test_run_worker_fails_before_leasing_unregistered_job(self):
        job_id = await self._enqueue("content.body")
        env = {
            "DATABASE_URL": self.database_url(),
            "FLYMAIL_DATA_DIR": "/tmp/flymail-v2-gate2-registry",
            "FLYMAIL_SESSION_SECRET": "gate2-worker-session-secret",
        }
        with patch.dict(os.environ, env, clear=False):
            with self.assertRaises(ConfigurationError):
                await run_worker(
                    stop_event=asyncio.Event(),
                    dispatcher=WorkerDispatcher(),
                    poll_seconds=0.01,
                )
        self.assertEqual(
            await self.scalar(
                "SELECT CONCAT(status, '|', lease_owner, '|', COALESCE(lease_token, '')) FROM worker_jobs WHERE id=%s",
                (job_id,),
            ),
            "pending||",
        )

class Gate2FakeProviderScenarioTests(MySqlIsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await run_migrations(self.api_pool)
        await self._clear_tables()
        self.temp_dir = tempfile.TemporaryDirectory(prefix="flymail-v2-gate2-")
        root = Path(self.temp_dir.name)
        self.store = ObjectStore(root / "objects", root / "tmp")
        self.registry = ProviderRegistry.default()
        self.tenant, self.account_a, self.inbox_a, self.archive_a = await self._create_scope()
        self.account_b, self.inbox_b, self.all_b = await self._create_gmail_scope()
        self.identity = await self._create_identity()
        self.ingestion = MessageIngestionService(self.api_pool)
        await self._ingest_initial_summaries()
        self.root_message_id = str(
            await self.scalar(
                "SELECT id FROM messages WHERE user_uid=%s AND message_id_header='<gate2-root@example.test>'",
                (self.tenant.user_uid,),
            )
        )
        self.reply_message_id = str(
            await self.scalar(
                "SELECT id FROM messages WHERE user_uid=%s AND message_id_header='<gate2-reply@example.test>'",
                (self.tenant.user_uid,),
            )
        )
        self.root_remote_id = str(
            await self.scalar(
                """
                SELECT id FROM message_remote_instances
                WHERE user_uid=%s AND message_id=%s AND account_id=%s
                ORDER BY id LIMIT 1
                """,
                (self.tenant.user_uid, self.root_message_id, self.account_a.id),
            )
        )
        self.content_transport = FakeContentTransport(
            content_fetch_fixtures.ContentFetchTests._responses()
        )
        self.content_service = ContentFetchService(
            self.api_pool,
            self.store,
            self.content_transport,
            ContentJobPublisher(self.api_pool),
            body_limit_bytes=2 * 1024 * 1024,
            attachment_limit_bytes=2 * 1024 * 1024,
            partial_chunk_bytes=4,
        )
        self.operation_gateway = FakeOperationGateway()
        self.operation_service = OperationService(self.api_pool, self.registry)
        self.operation_handler = OperationApplyHandler(
            self.worker_pool,
            self.operation_gateway,
            self.registry,
        )
        self.mail_gateway = FakeMailGateway()
        self.send_service = SendService(self.api_pool, self.store, self.registry)
        self.sender = ReliableSender(
            self.worker_pool,
            self.store,
            self.mail_gateway,
            self.registry,
            verification_retry_limit=1,
        )
        self.notification_secret = "gate2-notification-master-secret"
        self.notification_cipher = CredentialCipher.from_master_secret(
            self.notification_secret
        )
        self.http_transport = FakeHttpTransport()
        self.notification_service = NotificationService(self.api_pool)
        self.notification_handler = NotificationDeliveryHandler(
            self.worker_pool,
            self.store,
            self.notification_cipher,
            ChannelRegistry.default(
                self.http_transport,
                resolver=lambda _host, _port: ("8.8.8.8",),
            ),
            ImagePublisherRegistry.default(
                self.http_transport,
                resolver=lambda _host, _port: ("8.8.8.8",),
            ),
        )
        await self._create_notification_channel()

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()
        await super().asyncTearDown()

    async def _clear_tables(self) -> None:
        tables = (
            "notification_deliveries",
            "notification_events",
            "notification_rules",
            "notification_image_publishers",
            "notification_channels",
            "outbound_proxy_configs",
            "realtime_events",
            "send_attempts",
            "draft_attachments",
            "draft_recipients",
            "drafts",
            "mail_operations",
            "job_attempts",
            "worker_jobs",
            "outbox_events",
            "body_search_documents",
            "content_references",
            "content_objects",
            "message_attachments",
            "message_body_parts",
            "message_bodies",
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
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                for table in tables:
                    await cursor.execute(f"DELETE FROM {table}")
            await connection.commit()

    async def _create_scope(self):
        async with self.api_pool.acquire() as connection:
            await connection.begin()
            user = await UserRepository(connection).create_user_for_admin(
                AdminContext("usr_gate2_admin"),
                username="gate2-user",
                password_hash="test-password-hash",
            )
            tenant = TenantContext(user.id)
            account = await AccountRepository(connection).create_account(
                tenant,
                provider_key="generic",
                email="gate2-a@example.test",
                status="active",
            )
            inbox = await MailboxRepository(connection).upsert_mailbox(
                tenant,
                account_id=account.id,
                native_key="INBOX",
                native_name="Inbox",
                semantic_key="inbox",
                mailbox_type="folder",
                uidvalidity=77,
            )
            archive = await MailboxRepository(connection).upsert_mailbox(
                tenant,
                account_id=account.id,
                native_key="Archive",
                native_name="Archive",
                semantic_key="archive",
                mailbox_type="folder",
                uidvalidity=77,
            )
            await connection.commit()
        return tenant, account, inbox, archive

    async def _create_gmail_scope(self):
        async with self.api_pool.acquire() as connection:
            await connection.begin()
            account = await AccountRepository(connection).create_account(
                self.tenant,
                provider_key="gmail",
                email="gate2-b@example.test",
                status="active",
            )
            inbox = await MailboxRepository(connection).upsert_mailbox(
                self.tenant,
                account_id=account.id,
                native_key="\\Inbox",
                native_name="Inbox",
                semantic_key="inbox",
                mailbox_type="label",
                uidvalidity=88,
            )
            all_mail = await MailboxRepository(connection).upsert_mailbox(
                self.tenant,
                account_id=account.id,
                native_key="\\All",
                native_name="All Mail",
                semantic_key="all_mail",
                mailbox_type="label",
                uidvalidity=88,
            )
            await connection.commit()
        return account, inbox, all_mail

    async def _create_identity(self):
        async with self.api_pool.acquire() as connection:
            await connection.begin()
            identity = await IdentityRepository(connection).create_identity(
                self.tenant,
                self.account_a.id,
                from_address="gate2-a@example.test",
                display_name="Gate 2",
                is_default=True,
                is_verified=True,
            )
            await connection.commit()
        return identity

    async def _ingest_initial_summaries(self) -> None:
        await self.ingestion.ingest_batch(
            self.account_a,
            self.inbox_a,
            (
                RemoteSummary(
                    remote_uid=1,
                    uidvalidity=77,
                    message_id_header="<gate2-root@example.test>",
                    subject="Gate 2 thread",
                    from_addresses=("alice@example.test",),
                    to_addresses=("gate2-a@example.test",),
                    sent_at=100,
                    received_at=100,
                    size_bytes=4096,
                    has_attachments=True,
                    snippet="root metadata",
                    remote_version="a-v1",
                ),
            ),
        )
        reply = RemoteSummary(
            remote_uid=2,
            uidvalidity=88,
            message_id_header="<gate2-reply@example.test>",
            in_reply_to="<gate2-root@example.test>",
            references=("<gate2-root@example.test>",),
            subject="Re: Gate 2 thread",
            from_addresses=("bob@example.test",),
            to_addresses=("gate2-b@example.test",),
            sent_at=110,
            received_at=110,
            size_bytes=1024,
            snippet="reply metadata",
            provider_message_id="gmail-gate2-message",
            provider_thread_id="gmail-gate2-thread",
            remote_version="b-v1",
        )
        await self.ingestion.ingest_batch(self.account_b, self.inbox_b, (reply,))
        await self.ingestion.ingest_batch(self.account_b, self.all_b, (reply,))

    async def _store_draft_body(self, draft_id: str) -> str:
        stored = await self.store.put_stream(
            ObjectKind.BODY_TEXT,
            _one_chunk(b"Gate 2 outgoing body\n"),
            expected_size=21,
        )
        async with self.api_pool.acquire() as connection:
            await connection.begin()
            repository = ObjectRepository(connection)
            async with repository.lock_object(stored.content_sha256):
                await repository.attach_reference(
                    stored,
                    user_uid=self.tenant.user_uid,
                    reference_kind="draft_body_text",
                    reference_id=draft_id,
                    pinned=True,
                    last_accessed_at=200,
                )
            await connection.commit()
        return stored.content_sha256

    async def _create_draft(self, draft_id: str) -> None:
        body_hash = await self._store_draft_body(draft_id)
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO drafts (
                        id, user_uid, account_id, identity_id, subject,
                        body_text_object_sha256, version, status,
                        send_message_id, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, 'Gate 2 outgoing',
                              %s, 1, 'draft', '', 200, 200)
                    """,
                    (
                        draft_id,
                        self.tenant.user_uid,
                        self.account_a.id,
                        self.identity.id,
                        body_hash,
                    ),
                )
                await cursor.execute(
                    """
                    INSERT INTO draft_recipients (
                        id, draft_id, user_uid, recipient_kind,
                        address, normalized_address, display_name, position_index
                    ) VALUES (%s, %s, %s, 'to', 'receiver@example.test',
                              'receiver@example.test', 'Receiver', 0)
                    """,
                    (f"rcp_{draft_id}", draft_id, self.tenant.user_uid),
                )
            await connection.commit()

    async def _create_notification_channel(self) -> None:
        channel_specs = (
            (
                "chn_gate2_webhook",
                "generic_webhook",
                "Gate 2 webhook",
                {"endpoint_url": "https://notify.example/gate2"},
                {"authorization": "Bearer gate2-webhook-secret"},
            ),
            (
                "chn_gate2_telegram",
                "telegram",
                "Gate 2 Telegram",
                {"chat_id": "123456"},
                {"bot_token": "gate2-telegram-secret"},
            ),
        )
        rule_filter = json.dumps({"use_proxy": False, "image_enabled": False})
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                for channel_id, channel_key, display_name, public_config, secret_config in channel_specs:
                    encrypted = self.notification_cipher.encrypt(
                        channel_id,
                        json.dumps(secret_config, sort_keys=True).encode("utf-8"),
                    )
                    await cursor.execute(
                        """
                        INSERT INTO notification_channels (
                            id, user_uid, channel_key, display_name, enabled,
                            public_config, secret_algorithm, secret_key_version,
                            secret_nonce, secret_ciphertext, secret_auth_tag,
                            use_proxy, created_at, updated_at
                        ) VALUES (%s, %s, %s, %s, 1,
                                  %s, %s, %s, %s, %s, NULL, 0, 1, 1)
                        """,
                        (
                            channel_id,
                            self.tenant.user_uid,
                            channel_key,
                            display_name,
                            json.dumps(public_config),
                            encrypted.algorithm,
                            encrypted.key_version,
                            _decode_b64(encrypted.nonce_b64),
                            _decode_b64(encrypted.ciphertext_b64),
                        ),
                    )
                    for event_type in ("mail.new", "send.sent", "backup.completed"):
                        rule_id = f"rule_gate2_{channel_key}_{event_type.replace('.', '_')}"
                        await cursor.execute(
                            """
                            INSERT INTO notification_rules (
                                id, user_uid, event_type, channel_id,
                                enabled, filter_json, dedupe_window_seconds,
                                created_at, updated_at
                            ) VALUES (%s, %s, %s, %s, 1, %s, 0, 1, 1)
                            """,
                            (
                                rule_id,
                                self.tenant.user_uid,
                                event_type,
                                channel_id,
                                rule_filter,
                            ),
                        )
            await connection.commit()

    def leased(
        self,
        kind: str,
        payload: dict,
        *,
        account_id: str | None = None,
        provider_key: str | None = None,
        user_uid: str | None = None,
        attempt: int = 1,
    ) -> LeasedJob:
        return LeasedJob(
            id=f"job_gate2_{kind.replace('.', '_')}_{attempt}",
            user_uid=user_uid,
            account_id=account_id,
            provider_key=provider_key,
            queue_name="interactive",
            job_kind=kind,
            priority=1,
            available_at=1,
            lease_owner="worker_gate2",
            lease_token=f"lease_gate2_{kind}_{attempt}",
            lease_expires_at=1000,
            attempt_count=attempt,
            max_attempts=10,
            dedupe_key=f"gate2:{kind}:{attempt}",
            payload=payload,
        )

    async def scalar(self, sql: str, params: tuple | list = ()):
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(sql, params)
                row = await cursor.fetchone()
                return row[0] if row else None

    async def rows(self, sql: str, params: tuple | list = ()):
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(sql, params)
                return list(await cursor.fetchall())

    async def test_fake_provider_vertical_flow(self):
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM messages WHERE user_uid=%s",
                (self.tenant.user_uid,),
            ),
            2,
        )
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(DISTINCT thread_id) FROM thread_messages WHERE user_uid=%s",
                (self.tenant.user_uid,),
            ),
            1,
        )
        self.assertEqual(
            await self.scalar(
                """
                SELECT COUNT(*)
                FROM message_memberships mm
                JOIN message_remote_instances ri
                  ON ri.id = mm.remote_instance_id
                 AND ri.user_uid = mm.user_uid
                WHERE mm.user_uid=%s AND ri.message_id=%s
                """,
                (self.tenant.user_uid, self.reply_message_id),
            ),
            2,
        )

        idle_stop = asyncio.Event()
        idle_publisher = FakePublisher()
        snapshot = IdleAccountSnapshot(
            account_id=self.account_b.id,
            user_uid=self.tenant.user_uid,
            provider_key="gmail",
            mailbox_id=self.inbox_b.id,
            mailbox_native_key=self.inbox_b.native_key,
            credential_version=1,
            status="active",
            supports_idle=True,
            idle_refresh_seconds=60,
            poll_seconds=1,
        )
        supervisor = IdleSupervisor(
            FakeAccountSource(snapshot),
            FakeSessionFactory(
                (FakeIdleSession((IdleEvent("exists", count=1),), stop_event=idle_stop),)
            ),
            idle_publisher,
            AccountConnectionLimiter(self.registry),
            stop_event=idle_stop,
            reconnect_delay_seconds=0,
            state_check_seconds=0.01,
        )
        await supervisor.run_account(self.account_b.id)
        self.assertEqual(idle_publisher.incremental, ["message_exists"])

        await self.content_service.record_structure(
            self.tenant,
            message_id=self.root_message_id,
            remote_instance_id=self.root_remote_id,
            tree=content_fetch_fixtures.ContentFetchTests._content_tree(),
            now=120,
        )
        await self.content_service.request_body(
            self.tenant,
            self.root_message_id,
            now=121,
        )

        async def content_body(context, payload):
            await self.content_service.fetch_body(
                TenantContext(str(context.user_uid)),
                str(payload["message_id"]),
                now=122,
            )
            return JobOutcome.success()

        async def content_inline(context, payload):
            await self.content_service.fetch_inline(
                TenantContext(str(context.user_uid)),
                str(payload["attachment_id"]),
                now=123,
            )
            return JobOutcome.success()

        async def content_attachment(context, payload):
            await self.content_service.fetch_attachment(
                TenantContext(str(context.user_uid)),
                str(payload["attachment_id"]),
                supports_partial=True,
                now=124,
            )
            return JobOutcome.success()

        async def content_raw(context, payload):
            await self.content_service.fetch_raw_eml(
                TenantContext(str(context.user_uid)),
                str(payload["message_id"]),
                now=125,
            )
            return JobOutcome.success()

        handlers = {kind: _success_handler for kind in EXPECTED_JOB_KINDS}
        handlers.update(
            {
                "content.body": content_body,
                "content.inline": content_inline,
                "content.attachment": content_attachment,
                "content.raw_eml": content_raw,
                "mail.operation.apply": self.operation_handler,
                "send.deliver": self.sender.handle,
                "send.verify": self.sender.verify,
                "send.append_sent_copy": self.sender.append_sent_copy,
                "notification.deliver": self.notification_handler.handle,
            }
        )
        dispatcher = build_worker_dispatcher(handlers)
        body_outcome = await dispatcher.dispatch(
            self.leased(
                "content.body",
                {"message_id": self.root_message_id},
                user_uid=self.tenant.user_uid,
                account_id=self.account_a.id,
                provider_key="generic",
            ),
            stop_event=asyncio.Event(),
        )
        self.assertEqual(body_outcome.action, "complete")
        attachments = await self.rows(
            """
            SELECT id, imap_part, is_inline, is_referenced_inline
            FROM message_attachments
            WHERE user_uid=%s AND message_id=%s
            ORDER BY imap_part
            """,
            (self.tenant.user_uid, self.root_message_id),
        )
        inline_id = str(next(row[0] for row in attachments if row[1] == "1.2"))
        attachment_id = str(next(row[0] for row in attachments if row[1] == "2"))
        await self.content_service.request_attachment(
            self.tenant,
            attachment_id,
            now=123.5,
        )
        inline_outcome = await dispatcher.dispatch(
            self.leased(
                "content.inline",
                {"attachment_id": inline_id},
                user_uid=self.tenant.user_uid,
                account_id=self.account_a.id,
                provider_key="generic",
            ),
            stop_event=asyncio.Event(),
        )
        attachment_outcome = await dispatcher.dispatch(
            self.leased(
                "content.attachment",
                {"attachment_id": attachment_id},
                user_uid=self.tenant.user_uid,
                account_id=self.account_a.id,
                provider_key="generic",
            ),
            stop_event=asyncio.Event(),
        )
        self.assertEqual(
            (inline_outcome.action, attachment_outcome.action),
            ("complete", "complete"),
            msg=(attachment_outcome.error_class, attachment_outcome.error_message),
        )
        self.assertNotIn("BODY.PEEK[]", tuple(spec for _locator, spec in self.content_transport.calls))
        self.assertEqual(
            await self.scalar(
                "SELECT state FROM message_bodies WHERE message_id=%s",
                (self.root_message_id,),
            ),
            "ready",
        )
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM body_search_documents WHERE message_id=%s",
                (self.root_message_id,),
            ),
            1,
        )

        self.operation_gateway.states[self.root_remote_id] = RemoteOperationState(
            remote_version="a-v1",
            is_read=False,
            is_starred=False,
            mailbox_native_keys=("INBOX",),
        )
        read_operation = await self.operation_service.record_local_intent(
            self.tenant,
            remote_instance_id=self.root_remote_id,
            kind=OperationKind.SET_READ,
            desired_state={"value": True},
            idempotency_key="gate2-read",
            now=130,
        )
        read_outcome = await dispatcher.dispatch(
            self.leased(
                "mail.operation.apply",
                {"operation_id": read_operation},
                user_uid=self.tenant.user_uid,
                account_id=self.account_a.id,
                provider_key="generic",
            ),
            stop_event=asyncio.Event(),
        )
        self.assertEqual(read_outcome.action, "complete")
        applied_version = str(
            await self.scalar(
                "SELECT remote_version FROM message_remote_instances WHERE id=%s",
                (self.root_remote_id,),
            )
        )
        self.operation_gateway.states[self.root_remote_id] = RemoteOperationState(
            remote_version=applied_version,
            is_read=True,
            is_starred=False,
            mailbox_native_keys=("INBOX",),
        )
        move_operation = await self.operation_service.record_local_intent(
            self.tenant,
            remote_instance_id=self.root_remote_id,
            kind=OperationKind.MOVE,
            desired_state={"mailbox_id": self.archive_a.id},
            idempotency_key="gate2-move",
            now=131,
        )
        move_outcome = await dispatcher.dispatch(
            self.leased(
                "mail.operation.apply",
                {"operation_id": move_operation},
                user_uid=self.tenant.user_uid,
                account_id=self.account_a.id,
                provider_key="generic",
            ),
            stop_event=asyncio.Event(),
        )
        self.assertEqual(move_outcome.action, "complete")
        self.assertEqual(
            await self.scalar(
                """
                SELECT COUNT(*)
                FROM message_memberships mm
                JOIN message_remote_instances ri
                  ON ri.id = mm.remote_instance_id
                 AND ri.user_uid = mm.user_uid
                WHERE mm.user_uid=%s AND ri.message_id=%s AND mm.mailbox_id=%s
                """,
                (self.tenant.user_uid, self.root_message_id, self.archive_a.id),
            ),
            1,
        )

        draft_id = "drf_gate2_outgoing"
        await self._create_draft(draft_id)
        queued = await self.send_service.queue_draft(
            self.tenant,
            draft_id,
            idempotency_key="gate2-send",
            now=140,
        )
        self.mail_gateway.send_result = SmtpDeliveryUncertain("accepted state unknown")
        deliver_outcome = await dispatcher.dispatch(
            self.leased(
                "send.deliver",
                {"draft_id": draft_id, "operation_id": queued.operation_id},
                user_uid=self.tenant.user_uid,
                account_id=self.account_a.id,
                provider_key="generic",
            ),
            stop_event=asyncio.Event(),
        )
        self.assertEqual(deliver_outcome.action, "complete")
        self.mail_gateway.verify_result = SentVerificationResult(found=True, remote_uid=900)
        verify_outcome = await dispatcher.dispatch(
            self.leased(
                "send.verify",
                {"draft_id": draft_id, "operation_id": queued.operation_id},
                user_uid=self.tenant.user_uid,
                account_id=self.account_a.id,
                provider_key="generic",
                attempt=2,
            ),
            stop_event=asyncio.Event(),
        )
        self.assertEqual(verify_outcome.action, "complete")
        self.assertEqual(len(self.mail_gateway.send_calls), 1)
        self.assertEqual(
            await self.scalar("SELECT send_state FROM drafts WHERE id=%s", (draft_id,)),
            "sent",
        )

        notification_specs = (
            (
                "mail.new",
                self.root_message_id,
                "New message",
                "Gate 2 inbound completed",
                f"/mail/{self.root_message_id}",
                self.account_a.id,
                "gate2-new-mail-notification",
            ),
            (
                "send.sent",
                queued.operation_id,
                "Message sent",
                "Gate 2 outgoing completed",
                f"/drafts/{draft_id}",
                self.account_a.id,
                "gate2-send-notification",
            ),
            (
                "backup.completed",
                "backup_gate2",
                "Backup completed",
                "Gate 2 backup completed",
                "/settings/backup",
                None,
                "gate2-backup-notification",
            ),
        )
        event_ids: list[str] = []
        for index, (
            event_type,
            aggregate_id,
            title,
            summary,
            action_path,
            account_id,
            dedupe_key,
        ) in enumerate(notification_specs):
            published = await self.notification_service.publish(
                self.tenant,
                event_type=event_type,
                aggregate_id=aggregate_id,
                title=title,
                summary=summary,
                action_path=action_path,
                account_id=account_id,
                dedupe_key=dedupe_key,
                now=150 + index,
            )
            event_ids.append(published.event_id)
        deliveries = await self.rows(
            """
            SELECT nd.id, ne.account_id, a.provider_key
            FROM notification_deliveries nd
            JOIN notification_events ne
              ON ne.id = nd.notification_event_id
             AND ne.user_uid = nd.user_uid
            LEFT JOIN mail_accounts a
              ON a.id = ne.account_id AND a.user_uid = ne.user_uid
            WHERE nd.user_uid=%s
              AND nd.notification_event_id IN (%s, %s, %s)
            ORDER BY nd.notification_event_id, nd.channel_id
            """,
            (self.tenant.user_uid, *event_ids),
        )
        self.assertEqual(len(deliveries), 6)
        for attempt, (delivery_id, account_id, provider_key) in enumerate(deliveries, start=1):
            notification_outcome = await dispatcher.dispatch(
                self.leased(
                    "notification.deliver",
                    {"delivery_id": str(delivery_id)},
                    user_uid=self.tenant.user_uid,
                    account_id=str(account_id) if account_id else None,
                    provider_key=str(provider_key) if provider_key else None,
                    attempt=attempt,
                ),
                stop_event=asyncio.Event(),
            )
            self.assertEqual(notification_outcome.action, "complete")
        self.assertEqual(
            await self.scalar(
                """
                SELECT COUNT(*) FROM notification_deliveries
                WHERE user_uid=%s AND status='succeeded'
                  AND notification_event_id IN (%s, %s, %s)
                """,
                (self.tenant.user_uid, *event_ids),
            ),
            6,
        )
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM notification_events WHERE user_uid=%s AND id IN (%s, %s, %s)",
                (self.tenant.user_uid, *event_ids),
            ),
            3,
        )
        self.assertEqual(len(self.http_transport.requests), 6)
        self.assertEqual(dispatcher.registered_kinds, EXPECTED_JOB_KINDS)


if __name__ == "__main__":
    import unittest

    unittest.main()
