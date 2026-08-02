from __future__ import annotations

import json
import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

import httpx

from flymail.config import FlyMailSettings
from flymail.infrastructure.db.migrations.runner import run_migrations
from flymail.infrastructure.security.passwords import hash_password
from flymail.repositories.base import AdminContext
from flymail.repositories.users import UserRepository
from tests.v2.mysql_test_case import MySqlIsolatedAsyncioTestCase
from flymail.api.app import create_app


ORIGIN = "https://testserver"


class ComposeApiTests(MySqlIsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await run_migrations(self.api_pool)
        await self._clear_tables()
        self.temp_dir = tempfile.TemporaryDirectory(prefix="flymail-v2-compose-api-")
        root = Path(self.temp_dir.name)
        self.storage_root = root / "authorized"
        self.storage_root.mkdir(parents=True)
        (self.storage_root / "safe.txt").write_bytes(b"server-file-content")
        self.outside_file = root / "outside.txt"
        self.outside_file.write_bytes(b"outside-secret")
        os.symlink(self.outside_file, self.storage_root / "unsafe-link")
        self.settings = FlyMailSettings(
            role="api",
            database_url=self.database_url(),
            data_dir=root,
            object_dir=root / "objects" / "sha256",
            object_tmp_dir=root / "objects" / ".tmp",
            session_secret="compose-api-session-secret-0123456789abcdef",
            db_pool_name="flymail-api",
            db_min_connections=2,
            db_max_connections=12,
        )
        self.user = await self._create_user("compose-user", "ComposePassword!123")
        self.other = await self._create_user("compose-other", "OtherPassword!123")
        await self._seed_data()

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()
        await super().asyncTearDown()

    async def _clear_tables(self) -> None:
        tables = (
            "send_attempts", "draft_versions", "draft_attachments", "draft_recipients",
            "drafts", "job_attempts", "worker_jobs", "outbox_events", "mail_operations",
            "content_references", "content_objects", "message_memberships",
            "message_remote_instances", "messages", "threads", "mailboxes",
            "authorized_storage_roots", "account_runtime_state", "mail_identities",
            "mail_accounts", "login_rate_limits", "user_sessions", "user_profiles",
            "user_settings", "users", "process_heartbeats",
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
                AdminContext("usr_compose_test_admin"),
                username=username,
                password_hash=hash_password(password),
            )
            await connection.commit()
        return user

    async def _seed_data(self) -> None:
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.executemany(
                    """
                    INSERT INTO mail_accounts (
                        id, user_uid, provider_key, email, normalized_email,
                        display_name, status, poll_interval_seconds, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, 'active', 300, 1, 1)
                    """,
                    (
                        ("acc_compose_a", self.user.id, "gmail", "a@example.com", "a@example.com", "A"),
                        ("acc_compose_b", self.user.id, "outlook", "b@example.com", "b@example.com", "B"),
                        ("acc_compose_other", self.other.id, "gmail", "other@example.com", "other@example.com", "Other"),
                    ),
                )
                await cursor.executemany(
                    """
                    INSERT INTO mail_identities (
                        id, user_uid, account_id, from_address, normalized_from_address,
                        display_name, reply_to, is_default, is_verified, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, '', 1, 1, 1, 1)
                    """,
                    (
                        ("ident_compose_a", self.user.id, "acc_compose_a", "a@example.com", "a@example.com", "Sender A"),
                        ("ident_compose_b", self.user.id, "acc_compose_b", "b@example.com", "b@example.com", "Sender B"),
                        ("ident_compose_other", self.other.id, "acc_compose_other", "other@example.com", "other@example.com", "Other"),
                    ),
                )
                await cursor.execute(
                    """
                    INSERT INTO authorized_storage_roots (
                        id, user_uid, label, root_path, visibility_scope,
                        enabled, created_by, created_at, updated_at
                    ) VALUES ('root_compose', %s, 'Documents', %s, 'user', 1, %s, 1, 1)
                    """,
                    (self.user.id, str(self.storage_root), self.user.id),
                )
                await cursor.execute(
                    """
                    INSERT INTO threads (
                        id, user_uid, canonical_thread_key, normalized_subject,
                        created_at, updated_at
                    ) VALUES ('thr_compose_reply', %s, 'reply-thread', 'hello', 1, 1)
                    """,
                    (self.user.id,),
                )
                await cursor.execute(
                    """
                    INSERT INTO messages (
                        id, user_uid, canonical_message_key, thread_id,
                        subject, normalized_subject, from_json, to_json, cc_json,
                        reply_to_json, received_at, sent_at, snippet,
                        body_state, search_state, created_at, updated_at
                    ) VALUES ('msg_compose_reply', %s, 'reply-message',
                              'thr_compose_reply', 'Hello', 'hello',
                              '["sender@example.net"]', '["b@example.com"]', '[]',
                              '["reply@example.net"]', 10, 10, 'original snippet',
                              'not_requested', 'metadata', 1, 1)
                    """,
                    (self.user.id,),
                )
                await cursor.execute(
                    """
                    INSERT INTO mailboxes (
                        id, user_uid, account_id, native_key, native_name,
                        semantic_key, mailbox_type, sync_status, created_at, updated_at
                    ) VALUES ('mb_compose_b', %s, 'acc_compose_b', 'INBOX',
                              'Inbox', 'inbox', 'folder', 'ready', 1, 1)
                    """,
                    (self.user.id,),
                )
                await cursor.execute(
                    """
                    INSERT INTO message_remote_instances (
                        id, user_uid, account_id, mailbox_id, message_id,
                        uidvalidity, remote_uid, flags_json, is_read, is_starred,
                        remote_version, remote_deleted, last_seen_at,
                        created_at, updated_at
                    ) VALUES ('ri_compose_reply', %s, 'acc_compose_b',
                              'mb_compose_b', 'msg_compose_reply', 1, 10, '{}',
                              1, 0, 'v1', 0, 10, 1, 1)
                    """,
                    (self.user.id,),
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
        response = await client.post("/api/v2/auth/login", json={"username": username, "password": password})
        self.assertEqual(response.status_code, 200)
        return str(response.json()["csrf_token"])

    @staticmethod
    def csrf_headers(token: str) -> dict[str, str]:
        return {"Origin": ORIGIN, "X-CSRF-Token": token}

    @staticmethod
    def draft_payload(**overrides):
        value = {
            "account_id": "acc_compose_a",
            "identity_id": "ident_compose_a",
            "subject": "Draft subject",
            "body_html": "<p>Draft body</p>",
            "body_text": "Draft body",
            "recipients": {
                "to": [{"address": "to@example.net", "display_name": "To"}],
                "cc": [],
                "bcc": [{"address": "bcc@example.net", "display_name": "Bcc"}],
            },
            "scheduled_at": None,
        }
        value.update(overrides)
        return value

    async def create_draft(self, client, csrf, **overrides):
        response = await client.post(
            "/api/v2/drafts",
            headers=self.csrf_headers(csrf),
            json=self.draft_payload(**overrides),
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    async def test_draft_crud_uses_explicit_identity_and_is_tenant_scoped(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.100") as client:
                csrf = await self.login(client, "compose-user", "ComposePassword!123")
                invalid = await client.post(
                    "/api/v2/drafts",
                    headers=self.csrf_headers(csrf),
                    json=self.draft_payload(identity_id="ident_compose_other"),
                )
                created = await self.create_draft(client, csrf)
                loaded = await client.get(f"/api/v2/drafts/{created['id']}")
                missing = await client.get("/api/v2/drafts/draft_other")
                deleted = await client.delete(
                    f"/api/v2/drafts/{created['id']}",
                    headers=self.csrf_headers(csrf),
                )
        self.assertEqual(invalid.status_code, 404)
        self.assertEqual(loaded.status_code, 200)
        self.assertEqual(loaded.json()["account_id"], "acc_compose_a")
        self.assertEqual(loaded.json()["identity_id"], "ident_compose_a")
        self.assertEqual(loaded.json()["recipients"]["bcc"][0]["address"], "bcc@example.net")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(deleted.status_code, 204)

    async def test_reply_and_forward_template_select_receiving_account_identity(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.101") as client:
                await self.login(client, "compose-user", "ComposePassword!123")
                reply = await client.get(
                    "/api/v2/messages/msg_compose_reply/compose-template",
                    params={"mode": "reply"},
                )
                forward = await client.get(
                    "/api/v2/messages/msg_compose_reply/compose-template",
                    params={"mode": "forward"},
                )
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(reply.json()["account_id"], "acc_compose_b")
        self.assertEqual(reply.json()["identity_id"], "ident_compose_b")
        self.assertEqual(reply.json()["recipients"]["to"][0]["address"], "reply@example.net")
        self.assertTrue(reply.json()["subject"].startswith("Re:"))
        self.assertEqual(forward.status_code, 200)
        self.assertEqual(forward.json()["recipients"]["to"], [])
        self.assertTrue(forward.json()["subject"].startswith("Fwd:"))

    async def test_optimistic_save_preserves_current_and_conflict_versions(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.102") as client:
                csrf = await self.login(client, "compose-user", "ComposePassword!123")
                created = await self.create_draft(client, csrf)
                updated = await client.put(
                    f"/api/v2/drafts/{created['id']}",
                    headers=self.csrf_headers(csrf),
                    json={**self.draft_payload(subject="Current subject"), "expected_version": 1},
                )
                stale = await client.put(
                    f"/api/v2/drafts/{created['id']}",
                    headers=self.csrf_headers(csrf),
                    json={**self.draft_payload(subject="Stale subject"), "expected_version": 1},
                )
                loaded = await client.get(f"/api/v2/drafts/{created['id']}")
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["version"], 2)
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["error"]["code"], "draft_version_conflict")
        self.assertTrue(stale.json()["error"]["details"]["current_version_id"])
        self.assertTrue(stale.json()["error"]["details"]["incoming_version_id"])
        self.assertEqual(loaded.json()["subject"], "Current subject")
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM draft_versions WHERE draft_id=%s AND user_uid=%s",
                (created["id"], self.user.id),
            ),
            3,
        )

    async def test_stream_upload_and_shared_object_cleanup_are_reference_safe(self):
        payload = b"shared-draft-attachment"
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.103") as client:
                csrf = await self.login(client, "compose-user", "ComposePassword!123")
                first = await self.create_draft(client, csrf, subject="First")
                second = await self.create_draft(client, csrf, subject="Second")
                first_upload = await client.post(
                    f"/api/v2/drafts/{first['id']}/attachments",
                    headers={**self.csrf_headers(csrf), "X-Filename": "shared.bin", "Content-Type": "application/octet-stream"},
                    content=payload,
                )
                second_upload = await client.post(
                    f"/api/v2/drafts/{second['id']}/attachments",
                    headers={**self.csrf_headers(csrf), "X-Filename": "shared.bin", "Content-Type": "application/octet-stream"},
                    content=payload,
                )
                first_delete = await client.delete(
                    f"/api/v2/drafts/{first['id']}",
                    headers=self.csrf_headers(csrf),
                )
                digest = await self.scalar(
                    "SELECT content_sha256 FROM draft_attachments WHERE id=%s",
                    (second_upload.json()["id"],),
                )
                references_after_first = await self.scalar(
                    "SELECT COUNT(*) FROM content_references WHERE content_sha256=%s",
                    (digest,),
                )
                second_delete = await client.delete(
                    f"/api/v2/drafts/{second['id']}",
                    headers=self.csrf_headers(csrf),
                )
        self.assertEqual(first_upload.status_code, 201)
        self.assertEqual(second_upload.status_code, 201)
        self.assertNotIn("content_sha256", first_upload.json())
        self.assertEqual(first_delete.status_code, 204)
        self.assertEqual(int(references_after_first), 1)
        self.assertEqual(second_delete.status_code, 204)
        self.assertEqual(
            await self.scalar("SELECT COUNT(*) FROM content_objects WHERE content_sha256=%s", (digest,)),
            0,
        )

    async def test_authorized_storage_import_hides_paths_and_rejects_escape(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.104") as client:
                csrf = await self.login(client, "compose-user", "ComposePassword!123")
                draft = await self.create_draft(client, csrf)
                roots = await client.get("/api/v2/storage-roots")
                imported = await client.post(
                    f"/api/v2/drafts/{draft['id']}/attachments/import",
                    headers=self.csrf_headers(csrf),
                    json={"root_id": "root_compose", "relative_path": "safe.txt"},
                )
                traversal = await client.post(
                    f"/api/v2/drafts/{draft['id']}/attachments/import",
                    headers=self.csrf_headers(csrf),
                    json={"root_id": "root_compose", "relative_path": "../outside.txt"},
                )
                symlink = await client.post(
                    f"/api/v2/drafts/{draft['id']}/attachments/import",
                    headers=self.csrf_headers(csrf),
                    json={"root_id": "root_compose", "relative_path": "unsafe-link"},
                )
        self.assertEqual(roots.status_code, 200)
        self.assertNotIn(str(self.storage_root), roots.text)
        self.assertEqual(imported.status_code, 201)
        self.assertEqual(imported.json()["filename"], "safe.txt")
        self.assertNotIn("relative_path", imported.json())
        self.assertIn(traversal.status_code, {400, 403, 422})
        self.assertIn(symlink.status_code, {400, 403, 422})
        stored = await self.scalar(
            "SELECT COUNT(*) FROM content_objects WHERE object_kind='draft_attachment'"
        )
        self.assertEqual(int(stored), 1)

    async def test_send_schedule_idempotency_and_cancel_never_call_smtp(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.105") as client:
                csrf = await self.login(client, "compose-user", "ComposePassword!123")
                scheduled_at = 2_000_000_000.0
                draft = await self.create_draft(client, csrf, scheduled_at=scheduled_at)
                with patch("flymail.providers.core.smtp_client.SmtpMailGateway", side_effect=AssertionError("SMTP must not run in API")):
                    first = await client.post(
                        f"/api/v2/drafts/{draft['id']}/send",
                        headers=self.csrf_headers(csrf),
                        json={"idempotency_key": "send-compose-draft"},
                    )
                    repeated = await client.post(
                        f"/api/v2/drafts/{draft['id']}/send",
                        headers=self.csrf_headers(csrf),
                        json={"idempotency_key": "send-compose-draft"},
                    )
                cancelled = await client.post(
                    f"/api/v2/drafts/{draft['id']}/cancel-send",
                    headers=self.csrf_headers(csrf),
                    json={"operation_id": first.json()["operation_id"]},
                )
        self.assertEqual(first.status_code, 202)
        self.assertEqual(first.json(), repeated.json())
        self.assertNotIn("bcc@example.net", first.text)
        self.assertEqual(
            float(await self.scalar("SELECT available_at FROM worker_jobs WHERE id=%s", (first.json()["job_id"],))),
            scheduled_at,
        )
        self.assertEqual(cancelled.status_code, 204)
        self.assertEqual(
            await self.scalar("SELECT status FROM drafts WHERE id=%s", (draft["id"],)),
            "cancelled",
        )
        self.assertEqual(
            await self.scalar("SELECT status FROM worker_jobs WHERE id=%s", (first.json()["job_id"],)),
            "cancelled",
        )
