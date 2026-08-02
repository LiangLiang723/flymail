from __future__ import annotations

import json
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

import httpx

from flymail.config import FlyMailSettings
from flymail.infrastructure.db.migrations.runner import run_migrations
from flymail.infrastructure.security.passwords import hash_password
from flymail.repositories.base import AdminContext
from flymail.repositories.jobs import JobRepository, JobSpec
from flymail.repositories.users import UserRepository
from tests.v2.mysql_test_case import MySqlIsolatedAsyncioTestCase
from flymail.api.app import create_app


ORIGIN = "https://testserver"
MEBIBYTE = 1024 * 1024


class SettingsSyncApiTests(MySqlIsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await run_migrations(self.api_pool)
        await self._clear_tables()
        self.temp_dir = tempfile.TemporaryDirectory(prefix="flymail-v2-settings-sync-")
        root = Path(self.temp_dir.name)
        self.settings = FlyMailSettings(
            role="api",
            database_url=self.database_url(),
            data_dir=root,
            object_dir=root / "objects" / "sha256",
            object_tmp_dir=root / "objects" / ".tmp",
            session_secret="settings-sync-session-secret-0123456789abcdef",
            db_pool_name="flymail-api",
            db_min_connections=2,
            db_max_connections=12,
        )
        self.admin = await self._create_user("sync-admin", "AdminPassword!123", role="admin")
        self.user = await self._create_user("sync-user", "UserPassword!123")
        self.other = await self._create_user("sync-other", "OtherPassword!123")
        await self._seed_projection()

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()
        await super().asyncTearDown()

    async def _clear_tables(self) -> None:
        tables = (
            "realtime_events", "audit_events", "job_attempts", "worker_jobs",
            "sync_cursors", "account_runtime_state", "mail_operations",
            "content_references", "content_objects", "message_attachments",
            "message_bodies", "message_remote_instances", "message_memberships",
            "thread_projections", "thread_messages", "threads", "messages",
            "mailboxes", "mail_identities", "provider_credentials", "mail_accounts",
            "login_rate_limits", "user_sessions", "user_profiles", "user_settings",
            "users", "process_heartbeats",
        )
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                for table in tables:
                    await cursor.execute(f"DELETE FROM {table}")
            await connection.commit()

    async def _create_user(self, username: str, password: str, *, role: str = "user"):
        async with self.api_pool.acquire() as connection:
            await connection.begin()
            user = await UserRepository(connection).create_user_for_admin(
                AdminContext("usr_settings_sync_admin"),
                username=username,
                password_hash=hash_password(password),
                role=role,
            )
            await connection.commit()
        return user

    async def _seed_projection(self) -> None:
        digests = ("a" * 64, "b" * 64, "c" * 64)
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.executemany(
                    """
                    INSERT INTO mail_accounts (
                        id, user_uid, provider_key, email, normalized_email,
                        display_name, status, poll_interval_seconds, created_at, updated_at
                    ) VALUES (%s, %s, 'gmail', %s, %s, '', 'active', 300, 1, 1)
                    """,
                    (
                        ("acc_sync_user", self.user.id, "user@example.com", "user@example.com"),
                        ("acc_sync_other", self.other.id, "other@example.com", "other@example.com"),
                    ),
                )
                await cursor.execute(
                    """
                    INSERT INTO account_runtime_state (
                        account_id, user_uid, status, idle_status,
                        last_activity_at, last_change_at, next_reconcile_at,
                        failure_count, backoff_until, updated_at
                    ) VALUES ('acc_sync_user', %s, 'normal', 'idling', 9, 8, 10, 0, 0, 9)
                    """,
                    (self.user.id,),
                )
                for phase, progress in (
                    ("summary", {"completed": 8, "total": 10}),
                    ("body", {"completed": 5, "total": 10}),
                    ("index", {"completed": 4, "total": 10}),
                    ("state", {"completed": 9, "total": 10}),
                ):
                    await cursor.execute(
                        """
                        INSERT INTO sync_cursors (
                            id, user_uid, account_id, mailbox_id, phase,
                            cursor_type, cursor_json, last_uid, highest_modseq, updated_at
                        ) VALUES (%s, %s, 'acc_sync_user', '', %s, 'json', %s, 0, 0, 9)
                        """,
                        (
                            f"cursor_{phase}",
                            self.user.id,
                            phase,
                            json.dumps(progress, sort_keys=True),
                        ),
                    )
                await cursor.executemany(
                    """
                    INSERT INTO content_objects (
                        content_sha256, object_kind, compression,
                        original_size_bytes, stored_size_bytes, relative_path,
                        created_at
                    ) VALUES (%s, %s, 'none', %s, %s, %s, 1)
                    """,
                    (
                        (digests[0], "attachment", 100, 100, f"aa/{digests[0]}"),
                        (digests[1], "inline_image", 200, 200, f"bb/{digests[1]}"),
                        (digests[2], "body_text", 300, 300, f"cc/{digests[2]}"),
                    ),
                )
                await cursor.executemany(
                    """
                    INSERT INTO content_references (
                        id, user_uid, content_sha256, reference_kind,
                        reference_id, pinned, created_at, last_accessed_at
                    ) VALUES (%s, %s, %s, %s, %s, 0, 1, %s)
                    """,
                    (
                        ("ref_attach_1", self.user.id, digests[0], "message_attachment", "att_1", 1),
                        ("ref_attach_2", self.user.id, digests[0], "message_attachment", "att_2", 2),
                        ("ref_inline", self.user.id, digests[1], "message_inline_image", "att_inline", 3),
                        ("ref_body", self.user.id, digests[2], "message_body_text", "msg_body", 4),
                        ("ref_other", self.other.id, digests[0], "message_attachment", "att_other", 5),
                    ),
                )
                await cursor.executemany(
                    """
                    INSERT INTO mail_operations (
                        id, user_uid, operation_type, target_type, target_id,
                        account_id, desired_state, observed_remote_version,
                        status, priority, available_at, attempt_count,
                        last_error_class, last_error_message, idempotency_key,
                        created_at, updated_at
                    ) VALUES (%s, %s, %s, 'message', %s, %s, %s, '', %s,
                              100, 0, 0, '', '', %s, 1, 1)
                    """,
                    (
                        ("op_sync_conflict", self.user.id, "move", "msg_1", "acc_sync_user", '{"mailbox_id":"missing"}', "conflict", "sync-conflict"),
                        ("op_sync_pending", self.user.id, "mark_read", "msg_2", "acc_sync_user", '{"is_read":true}', "pending", "sync-pending"),
                        ("op_other_conflict", self.other.id, "move", "msg_other", "acc_sync_other", '{"mailbox_id":"missing"}', "conflict", "other-conflict"),
                    ),
                )
            await connection.commit()

    @asynccontextmanager
    async def running_app(self):
        app = create_app(self.settings)
        async with app.router.lifespan_context(app):
            yield app

    def client(self, app, source: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False, client=(source, 443)),
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

    async def test_settings_report_unique_usage_and_lowering_quota_enqueues_one_cleanup(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.130") as client:
                csrf = await self.login(client, "sync-user", "UserPassword!123")
                loaded = await client.get("/api/v2/settings")
                first = await client.put(
                    "/api/v2/settings",
                    headers=self.csrf_headers(csrf),
                    json={
                        "body_cache_quota_bytes": 100 * MEBIBYTE,
                        "attachment_cache_quota_bytes": 100 * MEBIBYTE,
                    },
                )
                client.cookies.clear()
                second_csrf = await self.login(
                    client,
                    "sync-user",
                    "UserPassword!123",
                )
                second = await client.put(
                    "/api/v2/settings",
                    headers=self.csrf_headers(second_csrf),
                    json={
                        "body_cache_quota_bytes": 100 * MEBIBYTE,
                        "attachment_cache_quota_bytes": 100 * MEBIBYTE,
                    },
                )
        self.assertEqual(loaded.status_code, 200)
        self.assertEqual(loaded.json()["body_cache_quota_bytes"], 5 * 1024**3)
        self.assertEqual(loaded.json()["body_cache_usage_bytes"], 500)
        self.assertEqual(loaded.json()["attachment_cache_usage_bytes"], 100)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertTrue(first.json()["cleanup_task_id"].startswith("job_"))
        self.assertEqual(second.status_code, 200, second.text)
        self.assertIsNone(second.json()["cleanup_task_id"])
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM worker_jobs WHERE job_kind='cache.cleanup' AND user_uid=%s",
                (self.user.id,),
            ),
            1,
        )

    async def test_zero_quota_is_unlimited_and_small_nonzero_quota_is_rejected(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.131") as client:
                csrf = await self.login(client, "sync-user", "UserPassword!123")
                unlimited = await client.put(
                    "/api/v2/settings",
                    headers=self.csrf_headers(csrf),
                    json={"body_cache_quota_bytes": 0, "attachment_cache_quota_bytes": 0},
                )
                too_small = await client.put(
                    "/api/v2/settings",
                    headers=self.csrf_headers(csrf),
                    json={"body_cache_quota_bytes": MEBIBYTE},
                )
        self.assertEqual(unlimited.status_code, 200)
        self.assertEqual(unlimited.json()["body_cache_quota_bytes"], 0)
        self.assertEqual(too_small.status_code, 422)

    async def test_sync_center_is_local_tenant_scoped_and_refresh_is_deduplicated(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.132") as client:
                csrf = await self.login(client, "sync-user", "UserPassword!123")
                center = await client.get("/api/v2/sync")
                first = await client.post(
                    "/api/v2/sync/accounts/acc_sync_user/refresh",
                    headers=self.csrf_headers(csrf),
                )
                second = await client.post(
                    "/api/v2/sync/accounts/acc_sync_user/refresh",
                    headers=self.csrf_headers(csrf),
                )
                foreign = await client.post(
                    "/api/v2/sync/accounts/acc_sync_other/refresh",
                    headers=self.csrf_headers(csrf),
                )
        self.assertEqual(center.status_code, 200)
        self.assertEqual([item["account_id"] for item in center.json()["accounts"]], ["acc_sync_user"])
        self.assertEqual(set(center.json()["accounts"][0]["phases"]), {"summary", "body", "index", "state"})
        self.assertEqual(center.json()["accounts"][0]["pending_operations"], 1)
        self.assertEqual(center.json()["accounts"][0]["conflicts"], 1)
        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.json()["task_id"], first.json()["task_id"])
        self.assertEqual(foreign.status_code, 404)

    async def test_conflict_resolution_is_tenant_scoped_audited_and_retries_locally(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.133") as client:
                csrf = await self.login(client, "sync-user", "UserPassword!123")
                listing = await client.get("/api/v2/sync/conflicts")
                resolved = await client.post(
                    "/api/v2/sync/conflicts/op_sync_conflict/resolve",
                    headers=self.csrf_headers(csrf),
                    json={"action": "retry_operation", "mailbox_id": "mailbox_archive"},
                )
                foreign = await client.post(
                    "/api/v2/sync/conflicts/op_other_conflict/resolve",
                    headers=self.csrf_headers(csrf),
                    json={"action": "cancel_operation"},
                )
        self.assertEqual([item["operation_id"] for item in listing.json()["items"]], ["op_sync_conflict"])
        self.assertEqual(resolved.status_code, 200)
        self.assertEqual(resolved.json()["status"], "pending")
        self.assertEqual(foreign.status_code, 404)
        desired = await self.scalar("SELECT desired_state FROM mail_operations WHERE id='op_sync_conflict'")
        desired_json = json.loads(desired) if isinstance(desired, str) else desired
        self.assertEqual(desired_json["mailbox_id"], "mailbox_archive")
        self.assertEqual(
            await self.scalar("SELECT COUNT(*) FROM audit_events WHERE resource_id='op_sync_conflict'"),
            1,
        )

    async def test_admin_diagnostics_are_aggregate_and_body_free(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.134") as client:
                user_csrf = await self.login(client, "sync-user", "UserPassword!123")
                denied = await client.get("/api/v2/admin/diagnostics")
                await client.post("/api/v2/auth/logout", headers=self.csrf_headers(user_csrf))
                await self.login(client, "sync-admin", "AdminPassword!123")
                diagnostics = await client.get("/api/v2/admin/diagnostics")
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(diagnostics.status_code, 200)
        self.assertEqual(
            set(diagnostics.json()),
            {"users", "accounts", "runnable_jobs", "failed_jobs", "conflicts", "worker_heartbeat_at"},
        )
        serialized = json.dumps(diagnostics.json()).casefold()
        for forbidden in ("body", "recipient", "password", "token", "ciphertext", "user@example.com"):
            self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    import unittest

    unittest.main()
