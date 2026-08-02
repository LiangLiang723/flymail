from __future__ import annotations

import asyncio
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

import httpx

from flymail.config import FlyMailSettings
from flymail.domain.enums import ObjectKind
from flymail.infrastructure.db.migrations.runner import run_migrations
from flymail.infrastructure.object_store.store import ObjectStore
from flymail.infrastructure.security.passwords import hash_password
from flymail.repositories.base import AdminContext
from flymail.repositories.users import UserRepository
from flymail.workers.bulk_operations import BulkMarkReadHandler
from flymail.workers.dispatcher import JobContext
from tests.v2.mysql_test_case import MySqlIsolatedAsyncioTestCase
from flymail.api.app import create_app


ORIGIN = "https://testserver"


async def _chunks(value: bytes):
    yield value


class OperationContentApiTests(MySqlIsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await run_migrations(self.api_pool)
        await self._clear_tables()
        self.temp_dir = tempfile.TemporaryDirectory(prefix="flymail-v2-op-content-api-")
        root = Path(self.temp_dir.name)
        self.settings = FlyMailSettings(
            role="api",
            database_url=self.database_url(),
            data_dir=root,
            object_dir=root / "objects" / "sha256",
            object_tmp_dir=root / "objects" / ".tmp",
            session_secret="operation-content-session-secret-0123456789abcdef",
            db_pool_name="flymail-api",
            db_min_connections=2,
            db_max_connections=12,
        )
        self.user = await self._create_user("operation-user", "OperationPassword!123")
        self.other = await self._create_user("operation-other", "OtherPassword!123")
        await self._seed_data()

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()
        await super().asyncTearDown()

    async def _clear_tables(self) -> None:
        tables = (
            "job_attempts",
            "bulk_mail_operations",
            "worker_jobs",
            "outbox_events",
            "mail_operations",
            "content_references",
            "content_objects",
            "message_attachments",
            "message_bodies",
            "thread_projections",
            "thread_messages",
            "message_memberships",
            "message_remote_instances",
            "messages",
            "threads",
            "mailboxes",
            "account_runtime_state",
            "mail_identities",
            "mail_accounts",
            "login_rate_limits",
            "user_sessions",
            "user_profiles",
            "user_settings",
            "users",
            "process_heartbeats",
        )
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                for table in tables:
                    await cursor.execute(f"DELETE FROM {table}")
            await connection.commit()

    async def _create_user(self, username: str, password: str):
        async with self.api_pool.acquire() as connection:
            await connection.begin()
            user = await UserRepository(connection).create_user_for_admin(
                AdminContext("usr_operation_test_admin"),
                username=username,
                password_hash=hash_password(password),
            )
            await connection.commit()
        return user

    async def _seed_data(self) -> None:
        store = ObjectStore(self.settings.object_dir, self.settings.object_tmp_dir)
        self.attachment_bytes = b"0123456789attachment"
        self.raw_bytes = b"From: sender@example.com\r\nSubject: Raw\r\n\r\nbody"
        attachment = await store.put_stream(
            ObjectKind.ATTACHMENT,
            _chunks(self.attachment_bytes),
        )
        raw = await store.put_stream(ObjectKind.RAW_EML, _chunks(self.raw_bytes))
        self.attachment_digest = attachment.content_sha256
        self.raw_digest = raw.content_sha256

        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.executemany(
                    """
                    INSERT INTO mail_accounts (
                        id, user_uid, provider_key, email, normalized_email,
                        display_name, status, poll_interval_seconds,
                        created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, 'active', 300, 1, 1)
                    """,
                    (
                        ("acc_operation_a", self.user.id, "gmail", "a@example.com", "a@example.com", "A"),
                        ("acc_operation_b", self.user.id, "outlook", "b@example.com", "b@example.com", "B"),
                        ("acc_operation_other", self.other.id, "gmail", "other@example.com", "other@example.com", "Other"),
                    ),
                )
                await cursor.executemany(
                    """
                    INSERT INTO account_runtime_state (
                        account_id, user_uid, status, idle_status,
                        last_activity_at, last_change_at, next_reconcile_at,
                        failure_count, backoff_until, updated_at
                    ) VALUES (%s, %s, 'normal', 'disconnected', 0, 0, 0, 0, 0, 0)
                    """,
                    (
                        ("acc_operation_a", self.user.id),
                        ("acc_operation_b", self.user.id),
                        ("acc_operation_other", self.other.id),
                    ),
                )
                await cursor.executemany(
                    """
                    INSERT INTO mailboxes (
                        id, user_uid, account_id, native_key, native_name,
                        semantic_key, mailbox_type, sync_status,
                        created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'ready', 1, 1)
                    """,
                    (
                        ("mb_operation_a_inbox", self.user.id, "acc_operation_a", "INBOX", "Inbox", "inbox", "folder"),
                        ("mb_operation_a_trash", self.user.id, "acc_operation_a", "TRASH", "Trash", "trash", "folder"),
                        ("mb_operation_a_label", self.user.id, "acc_operation_a", "Label/Project", "Project", "custom", "label"),
                        ("mb_operation_b_inbox", self.user.id, "acc_operation_b", "INBOX", "Inbox", "inbox", "folder"),
                        ("mb_operation_other", self.other.id, "acc_operation_other", "INBOX", "Inbox", "inbox", "folder"),
                    ),
                )
                await cursor.executemany(
                    """
                    INSERT INTO threads (
                        id, user_uid, canonical_thread_key,
                        normalized_subject, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, 1, 1)
                    """,
                    (
                        ("thr_operation_a", self.user.id, "op-a", "operation a"),
                        ("thr_operation_b", self.user.id, "op-b", "operation b"),
                        ("thr_operation_delete", self.user.id, "op-delete", "delete"),
                        ("thr_operation_other", self.other.id, "op-other", "other"),
                    ),
                )
                await cursor.executemany(
                    """
                    INSERT INTO messages (
                        id, user_uid, canonical_message_key, thread_id,
                        subject, normalized_subject, from_json, to_json,
                        received_at, sent_at, has_attachments, snippet,
                        body_state, search_state, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, '["sender@example.com"]',
                              '["recipient@example.com"]', %s, %s, %s, %s,
                              'not_requested', 'metadata', 1, 1)
                    """,
                    (
                        ("msg_operation_a", self.user.id, "key-a", "thr_operation_a", "Operation A", "operation a", 10, 10, 1, "a"),
                        ("msg_operation_b", self.user.id, "key-b", "thr_operation_b", "Operation B", "operation b", 20, 20, 1, "b"),
                        ("msg_operation_delete", self.user.id, "key-delete", "thr_operation_delete", "Delete", "delete", 30, 30, 0, "delete"),
                        ("msg_operation_other", self.other.id, "key-other", "thr_operation_other", "Other secret", "other", 40, 40, 0, "other-secret"),
                    ),
                )
                await cursor.executemany(
                    """
                    INSERT INTO thread_messages (
                        thread_id, message_id, user_uid,
                        relation_source, position_hint, created_at
                    ) VALUES (%s, %s, %s, 'headers', 1, 1)
                    """,
                    (
                        ("thr_operation_a", "msg_operation_a", self.user.id),
                        ("thr_operation_b", "msg_operation_b", self.user.id),
                        ("thr_operation_delete", "msg_operation_delete", self.user.id),
                        ("thr_operation_other", "msg_operation_other", self.other.id),
                    ),
                )
                await cursor.executemany(
                    """
                    INSERT INTO message_remote_instances (
                        id, user_uid, account_id, mailbox_id, message_id,
                        uidvalidity, remote_uid, flags_json, is_read, is_starred,
                        remote_version, remote_deleted, last_seen_at,
                        created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, 1, %s, '{}', 0, 0,
                              'version-1', 0, 1, 1, 1)
                    """,
                    (
                        ("ri_operation_a", self.user.id, "acc_operation_a", "mb_operation_a_inbox", "msg_operation_a", 11),
                        ("ri_operation_b", self.user.id, "acc_operation_b", "mb_operation_b_inbox", "msg_operation_b", 21),
                        ("ri_operation_delete", self.user.id, "acc_operation_a", "mb_operation_a_trash", "msg_operation_delete", 31),
                        ("ri_operation_other", self.other.id, "acc_operation_other", "mb_operation_other", "msg_operation_other", 41),
                    ),
                )
                await cursor.executemany(
                    """
                    INSERT INTO message_memberships (
                        remote_instance_id, mailbox_id, user_uid,
                        membership_kind, provider_label, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, 1, 1)
                    """,
                    (
                        ("ri_operation_a", "mb_operation_a_inbox", self.user.id, "folder", ""),
                        ("ri_operation_b", "mb_operation_b_inbox", self.user.id, "folder", ""),
                        ("ri_operation_delete", "mb_operation_a_trash", self.user.id, "folder", ""),
                        ("ri_operation_other", "mb_operation_other", self.other.id, "folder", ""),
                    ),
                )
                await cursor.executemany(
                    """
                    INSERT INTO thread_projections (
                        user_uid, semantic_mailbox, thread_id,
                        latest_message_id, latest_message_at, subject,
                        participants_summary, latest_snippet, message_count,
                        unread_count, is_starred, has_attachments,
                        account_count, pending_operation_count,
                        projection_version, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, '', '', 1, 1, 0, %s, 1, 0, 1, 1)
                    """,
                    (
                        (self.user.id, "inbox", "thr_operation_a", "msg_operation_a", 10, "Operation A", 1),
                        (self.user.id, "inbox", "thr_operation_b", "msg_operation_b", 20, "Operation B", 1),
                        (self.user.id, "trash", "thr_operation_delete", "msg_operation_delete", 30, "Delete", 0),
                        (self.other.id, "inbox", "thr_operation_other", "msg_operation_other", 40, "Other", 0),
                    ),
                )
                await cursor.executemany(
                    """
                    INSERT INTO content_objects (
                        content_sha256, object_kind, compression,
                        original_size_bytes, stored_size_bytes,
                        relative_path, verified_at, created_at
                    ) VALUES (%s, %s, 'none', %s, %s, %s, 1, 1)
                    """,
                    (
                        (
                            self.attachment_digest,
                            "attachment",
                            len(self.attachment_bytes),
                            len(self.attachment_bytes),
                            f"{self.attachment_digest[:2]}/{self.attachment_digest}",
                        ),
                        (
                            self.raw_digest,
                            "raw_eml",
                            len(self.raw_bytes),
                            len(self.raw_bytes),
                            f"{self.raw_digest[:2]}/{self.raw_digest}",
                        ),
                    ),
                )
                await cursor.executemany(
                    """
                    INSERT INTO content_references (
                        id, user_uid, content_sha256, reference_kind,
                        reference_id, pinned, created_at, last_accessed_at
                    ) VALUES (%s, %s, %s, %s, %s, 0, 1, 1)
                    """,
                    (
                        ("ref_operation_attachment", self.user.id, self.attachment_digest, "message_attachment", "att_operation_cached"),
                        ("ref_operation_raw", self.user.id, self.raw_digest, "raw_eml", "msg_operation_a"),
                    ),
                )
                await cursor.executemany(
                    """
                    INSERT INTO message_attachments (
                        id, user_uid, message_id, remote_instance_id,
                        imap_part, filename, content_type, disposition,
                        remote_size_bytes, content_sha256, is_inline,
                        cache_state, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'attachment',
                              %s, %s, 0, %s, 1, 1)
                    """,
                    (
                        (
                            "att_operation_cached",
                            self.user.id,
                            "msg_operation_a",
                            "ri_operation_a",
                            "2",
                            "../报告 2026.svg\r\n",
                            "image/svg+xml",
                            len(self.attachment_bytes),
                            self.attachment_digest,
                            "ready",
                        ),
                        (
                            "att_operation_missing",
                            self.user.id,
                            "msg_operation_b",
                            "ri_operation_b",
                            "3",
                            "missing.txt",
                            "text/plain",
                            99,
                            None,
                            "not_requested",
                        ),
                    ),
                )
                await cursor.execute(
                    """
                    INSERT INTO message_bodies (
                        message_id, user_uid, raw_eml_object_sha256,
                        state, body_size_bytes, index_version, parser_version,
                        checked_at, cached_at, last_accessed_at, updated_at
                    ) VALUES ('msg_operation_a', %s, %s, 'ready', %s, 1, 1, 1, 1, 1, 1)
                    """,
                    (self.user.id, self.raw_digest, len(self.raw_bytes)),
                )
            await connection.commit()

    @asynccontextmanager
    async def running_app(self):
        app = create_app(self.settings)
        async with app.router.lifespan_context(app):
            yield app

    def client(self, app, source: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=app,
                raise_app_exceptions=False,
                client=(source, 443),
            ),
            base_url=ORIGIN,
        )

    async def login(self, client: httpx.AsyncClient, username: str, password: str) -> str:
        response = await client.post(
            "/api/v2/auth/login",
            json={"username": username, "password": password},
        )
        self.assertEqual(response.status_code, 200)
        return str(response.json()["csrf_token"])

    @staticmethod
    def csrf_headers(token: str) -> dict[str, str]:
        return {"Origin": ORIGIN, "X-CSRF-Token": token}

    async def test_operation_requires_csrf_and_is_idempotent_with_local_projection(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.80") as client:
                csrf = await self.login(client, "operation-user", "OperationPassword!123")
                body = {
                    "target_type": "remote_instance",
                    "target_id": "ri_operation_a",
                    "operation_type": "set_read",
                    "desired_state": {"value": True},
                    "idempotency_key": "read-operation-a",
                }
                missing_csrf = await client.post("/api/v2/operations", json=body)
                first = await client.post(
                    "/api/v2/operations",
                    headers=self.csrf_headers(csrf),
                    json=body,
                )
                second = await client.post(
                    "/api/v2/operations",
                    headers=self.csrf_headers(csrf),
                    json=body,
                )
                conflict = await client.post(
                    "/api/v2/operations",
                    headers=self.csrf_headers(csrf),
                    json={**body, "desired_state": {"value": False}},
                )
                cross_tenant = await client.post(
                    "/api/v2/operations",
                    headers=self.csrf_headers(csrf),
                    json={**body, "target_id": "ri_operation_other", "idempotency_key": "cross"},
                )

        self.assertEqual(missing_csrf.status_code, 403)
        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(first.json()["operation_ids"], second.json()["operation_ids"])
        self.assertEqual(
            [item["status"] for item in first.json()["items"]],
            ["pending"],
        )
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(cross_tenant.status_code, 404)
        self.assertEqual(
            await self.scalar(
                "SELECT is_read FROM message_remote_instances WHERE id='ri_operation_a'"
            ),
            1,
        )
        operation_id = first.json()["operation_ids"][0]
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM mail_operations WHERE id=%s AND status='pending'",
                (operation_id,),
            ),
            1,
        )
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM worker_jobs WHERE job_kind='mail.operation.apply' AND JSON_UNQUOTE(JSON_EXTRACT(payload, '$.operation_id'))=%s",
                (operation_id,),
            ),
            1,
        )
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM outbox_events WHERE aggregate_id=%s AND event_type='mail.operation.pending'",
                (operation_id,),
            ),
            1,
        )

    async def test_thread_operation_undo_and_permanent_delete_confirmation(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.81") as client:
                csrf = await self.login(client, "operation-user", "OperationPassword!123")
                archived = await client.post(
                    "/api/v2/operations",
                    headers=self.csrf_headers(csrf),
                    json={
                        "target_type": "thread",
                        "target_id": "thr_operation_a",
                        "operation_type": "archive",
                        "desired_state": {},
                        "idempotency_key": "archive-thread-a",
                    },
                )
                operation_id = archived.json()["operation_ids"][0]
                undone = await client.post(
                    f"/api/v2/operations/{operation_id}/undo",
                    headers=self.csrf_headers(csrf),
                    json={"idempotency_key": "undo-archive-thread-a"},
                )
                denied_delete = await client.post(
                    "/api/v2/operations",
                    headers=self.csrf_headers(csrf),
                    json={
                        "target_type": "remote_instance",
                        "target_id": "ri_operation_delete",
                        "operation_type": "delete_permanent",
                        "desired_state": {},
                        "idempotency_key": "delete-without-confirmation",
                    },
                )
                confirmation = await client.post(
                    "/api/v2/operations/permanent-delete-confirmation",
                    headers=self.csrf_headers(csrf),
                    json={
                        "target_type": "remote_instance",
                        "target_id": "ri_operation_delete",
                    },
                )
                accepted_delete = await client.post(
                    "/api/v2/operations",
                    headers=self.csrf_headers(csrf),
                    json={
                        "target_type": "remote_instance",
                        "target_id": "ri_operation_delete",
                        "operation_type": "delete_permanent",
                        "desired_state": {},
                        "idempotency_key": "delete-confirmed",
                        "confirmation_token": confirmation.json()["confirmation_token"],
                    },
                )

        self.assertEqual(archived.status_code, 202)
        self.assertEqual(archived.json()["items"][0]["status"], "pending")
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM outbox_events WHERE aggregate_id=%s AND event_type='mail.operation.group.pending'",
                (archived.json()["operation_group_id"],),
            ),
            1,
        )
        self.assertEqual(undone.status_code, 200)
        self.assertEqual(undone.json()["operation_id"], operation_id)
        self.assertEqual(
            await self.scalar("SELECT status FROM mail_operations WHERE id=%s", (operation_id,)),
            "cancelled",
        )
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM message_memberships WHERE remote_instance_id='ri_operation_a' AND mailbox_id='mb_operation_a_inbox'"
            ),
            1,
        )
        self.assertEqual(denied_delete.status_code, 409)
        self.assertEqual(confirmation.status_code, 200)
        self.assertGreater(confirmation.json()["expires_at"], 0)
        self.assertEqual(accepted_delete.status_code, 202)
        self.assertEqual(
            await self.scalar(
                "SELECT remote_deleted FROM message_remote_instances WHERE id='ri_operation_delete'"
            ),
            1,
        )

    async def test_permanent_delete_confirmation_is_invalidated_by_state_change(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.85") as client:
                csrf = await self.login(client, "operation-user", "OperationPassword!123")
                confirmation = await client.post(
                    "/api/v2/operations/permanent-delete-confirmation",
                    headers=self.csrf_headers(csrf),
                    json={
                        "target_type": "remote_instance",
                        "target_id": "ri_operation_delete",
                    },
                )
                self.assertEqual(confirmation.status_code, 200)
                async with self.api_pool.acquire() as connection:
                    async with connection.cursor() as cursor:
                        await cursor.execute(
                            "DELETE FROM message_memberships WHERE remote_instance_id='ri_operation_delete' AND user_uid=%s",
                            (self.user.id,),
                        )
                    await connection.commit()
                invalidated = await client.post(
                    "/api/v2/operations",
                    headers=self.csrf_headers(csrf),
                    json={
                        "target_type": "remote_instance",
                        "target_id": "ri_operation_delete",
                        "operation_type": "delete_permanent",
                        "desired_state": {},
                        "idempotency_key": "delete-after-state-change",
                        "confirmation_token": confirmation.json()["confirmation_token"],
                    },
                )
        self.assertEqual(invalidated.status_code, 409)
        self.assertEqual(
            invalidated.json()["error"]["code"],
            "invalid_confirmation_token",
        )
        self.assertEqual(
            await self.scalar(
                "SELECT remote_deleted FROM message_remote_instances WHERE id='ri_operation_delete'"
            ),
            0,
        )

    async def test_permanent_delete_confirmation_expires(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.86") as client:
                csrf = await self.login(client, "operation-user", "OperationPassword!123")
                confirmation = await client.post(
                    "/api/v2/operations/permanent-delete-confirmation",
                    headers=self.csrf_headers(csrf),
                    json={
                        "target_type": "remote_instance",
                        "target_id": "ri_operation_delete",
                    },
                )
                self.assertEqual(confirmation.status_code, 200)
                app.state.mail_operation_api_service.now_fn = lambda: (
                    float(confirmation.json()["expires_at"]) + 1
                )
                expired = await client.post(
                    "/api/v2/operations",
                    headers=self.csrf_headers(csrf),
                    json={
                        "target_type": "remote_instance",
                        "target_id": "ri_operation_delete",
                        "operation_type": "delete_permanent",
                        "desired_state": {},
                        "idempotency_key": "delete-after-expiry",
                        "confirmation_token": confirmation.json()["confirmation_token"],
                    },
                )
        self.assertEqual(expired.status_code, 409)
        self.assertEqual(expired.json()["error"]["code"], "invalid_confirmation_token")

    async def test_query_scoped_mark_all_read_updates_only_matching_account(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.82") as client:
                csrf = await self.login(client, "operation-user", "OperationPassword!123")
                body = {
                    "semantic_mailbox": "inbox",
                    "account_id": "acc_operation_a",
                    "idempotency_key": "mark-account-a-read",
                }
                response = await client.post(
                    "/api/v2/operations/mark-all-read",
                    headers=self.csrf_headers(csrf),
                    json=body,
                )
                repeated = await client.post(
                    "/api/v2/operations/mark-all-read",
                    headers=self.csrf_headers(csrf),
                    json=body,
                )
                conflict = await client.post(
                    "/api/v2/operations/mark-all-read",
                    headers=self.csrf_headers(csrf),
                    json={**body, "account_id": "acc_operation_b"},
                )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(repeated.status_code, 202)
        self.assertEqual(response.json(), repeated.json())
        self.assertEqual(conflict.status_code, 409)
        bulk_id = str(response.json()["bulk_operation_id"])
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM bulk_mail_operations WHERE id=%s AND status='pending'",
                (bulk_id,),
            ),
            1,
        )
        self.assertEqual(
            await self.scalar("SELECT is_read FROM message_remote_instances WHERE id='ri_operation_a'"),
            0,
        )
        handler = BulkMarkReadHandler(self.api_pool, batch_size=1)
        first = await handler(
            JobContext(
                job_id=str(response.json()["job_id"]),
                user_uid=self.user.id,
                account_id=None,
                provider_key=None,
                queue_name="operations",
                worker_id="wrk_bulk_test",
                attempt_count=1,
                stop_event=asyncio.Event(),
            ),
            {"bulk_operation_id": bulk_id},
        )
        second = await handler(
            JobContext(
                job_id=str(response.json()["job_id"]),
                user_uid=self.user.id,
                account_id=None,
                provider_key=None,
                queue_name="operations",
                worker_id="wrk_bulk_test",
                attempt_count=2,
                stop_event=asyncio.Event(),
            ),
            {"bulk_operation_id": bulk_id},
        )
        self.assertEqual(first.action, "retry")
        self.assertEqual(second.action, "complete")
        self.assertEqual(
            await self.scalar("SELECT is_read FROM message_remote_instances WHERE id='ri_operation_a'"),
            1,
        )
        self.assertEqual(
            await self.scalar("SELECT is_read FROM message_remote_instances WHERE id='ri_operation_b'"),
            0,
        )
        self.assertEqual(
            await self.scalar("SELECT status FROM bulk_mail_operations WHERE id=%s", (bulk_id,)),
            "completed",
        )

    async def test_attachment_metadata_stream_range_and_missing_request_are_safe(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.83") as client:
                csrf = await self.login(client, "operation-user", "OperationPassword!123")
                metadata = await client.get("/api/v2/attachments/att_operation_cached")
                full = await client.get("/api/v2/attachments/att_operation_cached/content")
                partial = await client.get(
                    "/api/v2/attachments/att_operation_cached/content",
                    headers={"Range": "bytes=2-5"},
                )
                first_missing = await client.post(
                    "/api/v2/attachments/att_operation_missing/request",
                    headers=self.csrf_headers(csrf),
                )
                second_missing = await client.get(
                    "/api/v2/attachments/att_operation_missing/content"
                )
                cross_tenant = await client.get(
                    "/api/v2/attachments/att_operation_other/content"
                )

        self.assertEqual(metadata.status_code, 200)
        self.assertNotIn("content_sha256", metadata.json())
        self.assertEqual(full.status_code, 200)
        self.assertEqual(full.content, self.attachment_bytes)
        self.assertEqual(full.headers["content-type"], "image/svg+xml")
        self.assertIn("attachment;", full.headers["content-disposition"])
        self.assertIn("filename*=UTF-8''", full.headers["content-disposition"])
        self.assertNotIn("%2F", full.headers["content-disposition"].casefold())
        self.assertNotIn("\r", full.headers["content-disposition"])
        self.assertNotIn("\n", full.headers["content-disposition"])
        self.assertEqual(full.headers["x-content-type-options"], "nosniff")
        self.assertEqual(partial.status_code, 206)
        self.assertEqual(partial.content, self.attachment_bytes[2:6])
        self.assertEqual(
            partial.headers["content-range"],
            f"bytes 2-5/{len(self.attachment_bytes)}",
        )
        self.assertEqual(first_missing.status_code, 202)
        self.assertEqual(second_missing.status_code, 202)
        self.assertEqual(first_missing.json()["job_id"], second_missing.json()["job_id"])
        self.assertEqual(cross_tenant.status_code, 404)
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM worker_jobs WHERE job_kind='content.attachment' AND JSON_UNQUOTE(JSON_EXTRACT(payload, '$.attachment_id'))='att_operation_missing'"
            ),
            1,
        )

    async def test_raw_eml_status_stream_and_missing_request_are_tenant_scoped(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.84") as client:
                csrf = await self.login(client, "operation-user", "OperationPassword!123")
                status = await client.get("/api/v2/messages/msg_operation_a/raw")
                content = await client.get("/api/v2/messages/msg_operation_a/raw/content")
                requested = await client.post(
                    "/api/v2/messages/msg_operation_b/raw/request",
                    headers=self.csrf_headers(csrf),
                )
                repeated = await client.get("/api/v2/messages/msg_operation_b/raw/content")
                cross_tenant = await client.get(
                    "/api/v2/messages/msg_operation_other/raw/content"
                )

        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["state"], "ready")
        self.assertEqual(content.status_code, 200)
        self.assertEqual(content.headers["content-type"], "message/rfc822")
        self.assertEqual(content.content, self.raw_bytes)
        self.assertEqual(requested.status_code, 202)
        self.assertEqual(repeated.status_code, 202)
        self.assertEqual(requested.json()["job_id"], repeated.json()["job_id"])
        self.assertEqual(cross_tenant.status_code, 404)
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM worker_jobs WHERE job_kind='content.raw_eml' AND JSON_UNQUOTE(JSON_EXTRACT(payload, '$.message_id'))='msg_operation_b'"
            ),
            1,
        )
