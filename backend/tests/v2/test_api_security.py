from __future__ import annotations

import json
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

import httpx

from flymail.application.auth import SESSION_COOKIE_NAME
from flymail.config import FlyMailSettings
from flymail.infrastructure.db.migrations.runner import run_migrations
from flymail.infrastructure.security.passwords import hash_password
from flymail.repositories.base import AdminContext
from flymail.repositories.users import UserRepository
from tests.v2.mysql_test_case import MySqlIsolatedAsyncioTestCase
from flymail.api.app import create_app


ORIGIN = "https://testserver"


class ApiSecurityTests(MySqlIsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await run_migrations(self.api_pool)
        await self._clear_tables()
        self.temp_dir = tempfile.TemporaryDirectory(prefix="flymail-v2-security-")
        root = Path(self.temp_dir.name)
        self.private_root = root / "private-root"
        self.private_root.mkdir()
        (self.private_root / "private.txt").write_text("private", encoding="utf-8")
        self.settings = FlyMailSettings(
            role="api",
            database_url=self.database_url(),
            data_dir=root,
            object_dir=root / "objects" / "sha256",
            object_tmp_dir=root / "objects" / ".tmp",
            session_secret="security-session-secret-0123456789abcdef",
            db_pool_name="flymail-security",
            db_min_connections=2,
            db_max_connections=12,
        )
        self.owner = await self._create_user("security-owner", "OwnerPassword!123")
        self.other = await self._create_user("security-other", "OtherPassword!123")
        self.admin = await self._create_user("security-admin", "AdminPassword!123", role="admin")
        await self._seed_owner_resources()

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()
        await super().asyncTearDown()

    async def _clear_tables(self) -> None:
        tables = (
            "backup_archives", "notification_deliveries", "notification_events",
            "notification_rules", "notification_image_publishers", "notification_channels",
            "saved_searches", "search_history", "draft_attachments", "draft_recipients",
            "draft_versions", "drafts", "mail_operations", "worker_jobs", "job_attempts",
            "outbox_events", "realtime_events", "message_attachments", "message_bodies",
            "content_references", "content_objects", "thread_projections", "thread_messages",
            "message_memberships", "message_remote_instances", "message_headers", "messages",
            "threads", "mailboxes", "contacts", "authorized_storage_roots", "mail_identities",
            "provider_credentials", "mail_accounts", "audit_events", "login_rate_limits",
            "user_sessions", "user_profiles", "user_settings", "users", "process_heartbeats",
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
                AdminContext("usr_security_admin"),
                username=username,
                password_hash=hash_password(password),
                role=role,
            )
            await connection.commit()
        return user

    async def _seed_owner_resources(self) -> None:
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO mail_accounts (
                        id,user_uid,provider_key,email,normalized_email,display_name,
                        status,icon_mode,icon_value,poll_interval_seconds,created_at,updated_at
                    ) VALUES ('acc_owner',%s,'gmail','owner@example.com','owner@example.com',
                              'Owner','active','provider','',300,1,1)
                    """,
                    (self.owner.id,),
                )
                await cursor.execute(
                    """
                    INSERT INTO mail_identities (
                        id,user_uid,account_id,from_address,normalized_from_address,
                        display_name,is_default,is_verified,created_at,updated_at
                    ) VALUES ('identity_owner',%s,'acc_owner','owner@example.com',
                              'owner@example.com','Owner',1,1,1,1)
                    """,
                    (self.owner.id,),
                )
                await cursor.execute(
                    """
                    INSERT INTO mailboxes (
                        id,user_uid,account_id,native_key,native_name,
                        semantic_key,mailbox_type,sync_status,created_at,updated_at
                    ) VALUES ('mailbox_owner',%s,'acc_owner','INBOX','Inbox',
                              'inbox','folder','ready',1,1)
                    """,
                    (self.owner.id,),
                )
                await cursor.execute(
                    """
                    INSERT INTO threads (id,user_uid,canonical_thread_key,normalized_subject,created_at,updated_at)
                    VALUES ('thread_owner',%s,'thread-owner','secret subject',1,1)
                    """,
                    (self.owner.id,),
                )
                await cursor.execute(
                    """
                    INSERT INTO messages (
                        id,user_uid,canonical_message_key,thread_id,subject,normalized_subject,
                        from_json,to_json,received_at,body_state,created_at,updated_at
                    ) VALUES ('message_owner',%s,'message-owner','thread_owner','Secret',
                              'secret','[]','[]',1,'not_requested',1,1)
                    """,
                    (self.owner.id,),
                )
                await cursor.execute(
                    """
                    INSERT INTO message_remote_instances (
                        id,user_uid,account_id,mailbox_id,message_id,uidvalidity,remote_uid,
                        is_read,is_starred,remote_version,remote_deleted,created_at,updated_at
                    ) VALUES ('remote_owner',%s,'acc_owner','mailbox_owner','message_owner',1,1,
                              0,0,'v1',0,1,1)
                    """,
                    (self.owner.id,),
                )
                await cursor.execute(
                    """
                    INSERT INTO message_attachments (
                        id,user_uid,message_id,remote_instance_id,imap_part,filename,
                        remote_size_bytes,is_inline,cache_state,created_at,updated_at
                    ) VALUES ('attachment_owner',%s,'message_owner','remote_owner','1',
                              'secret.txt',10,0,'not_requested',1,1)
                    """,
                    (self.owner.id,),
                )
                await cursor.execute(
                    """
                    INSERT INTO drafts (
                        id,user_uid,account_id,identity_id,subject,version,status,send_state,
                        created_at,updated_at
                    ) VALUES ('draft_owner',%s,'acc_owner','identity_owner','Secret draft',1,
                              'draft','draft',1,1)
                    """,
                    (self.owner.id,),
                )
                await cursor.execute(
                    """
                    INSERT INTO contacts (
                        id,user_uid,display_name,normalized_name,primary_email,
                        normalized_email,emails_json,created_at,updated_at
                    ) VALUES ('contact_owner',%s,'Secret Contact','secret contact',
                              'secret@example.com','secret@example.com','["secret@example.com"]',1,1)
                    """,
                    (self.owner.id,),
                )
                await cursor.execute(
                    """
                    INSERT INTO saved_searches (id,user_uid,name,filters_json,is_pinned,created_at,updated_at)
                    VALUES ('search_owner',%s,'Secret Search','{"keyword":"secret"}',0,1,1)
                    """,
                    (self.owner.id,),
                )
                await cursor.execute(
                    """
                    INSERT INTO notification_events (
                        id,user_uid,event_type,title,summary,action_path,dedupe_key,created_at
                    ) VALUES ('notification_owner',%s,'mail.new','Secret notification','',
                              '/inbox','security-notification',1)
                    """,
                    (self.owner.id,),
                )
                await cursor.execute(
                    """
                    INSERT INTO mail_operations (
                        id,user_uid,operation_type,target_type,target_id,account_id,
                        desired_state,status,priority,available_at,idempotency_key,created_at,updated_at
                    ) VALUES ('operation_owner',%s,'set_read','remote_instance','remote_owner',
                              'acc_owner','{"is_read":true}','pending',100,0,'security-operation',1,1)
                    """,
                    (self.owner.id,),
                )
                await cursor.execute(
                    """
                    INSERT INTO authorized_storage_roots (
                        id,user_uid,label,root_path,visibility_scope,enabled,created_by,created_at,updated_at
                    ) VALUES ('storage_owner',%s,'Private',%s,'user',1,%s,1,1)
                    """,
                    (self.owner.id, str(self.private_root), self.admin.id),
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
        self.assertEqual(response.status_code, 200, response.text)
        return str(response.json()["csrf_token"])

    @staticmethod
    def csrf(token: str, origin: str = ORIGIN) -> dict[str, str]:
        return {"Origin": origin, "X-CSRF-Token": token}

    async def _assert_owner_ids_are_hidden(self, client: httpx.AsyncClient, csrf: str) -> None:
        requests = (
            ("GET", "/api/v2/accounts/acc_owner", None),
            ("GET", "/api/v2/accounts/acc_owner/icon/content", None),
            ("GET", "/api/v2/threads/thread_owner", None),
            ("GET", "/api/v2/messages/message_owner/body", None),
            ("GET", "/api/v2/attachments/attachment_owner", None),
            ("GET", "/api/v2/drafts/draft_owner", None),
            ("PATCH", "/api/v2/contacts/contact_owner", {"display_name": "Changed"}),
            ("PATCH", "/api/v2/saved-searches/search_owner", {"name": "Changed"}),
            ("POST", "/api/v2/notifications/notification_owner/read", {}),
            ("POST", "/api/v2/operations/operation_owner/undo", {"idempotency_key": "foreign-undo"}),
            ("GET", "/api/v2/storage/roots/storage_owner/browse", None),
        )
        for method, path, body in requests:
            response = await client.request(
                method,
                path,
                headers=self.csrf(csrf) if method not in {"GET", "HEAD"} else None,
                json=body,
            )
            with self.subTest(method=method, path=path):
                self.assertIn(response.status_code, {403, 404})
                rendered = response.text.casefold()
                for forbidden in ("secret subject", "secret draft", "secret@example.com", str(self.private_root).casefold()):
                    self.assertNotIn(forbidden, rendered)

    async def test_guessed_ids_are_non_enumerating_for_other_user_and_admin(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.160") as client:
                other_csrf = await self.login(client, "security-other", "OtherPassword!123")
                await self._assert_owner_ids_are_hidden(client, other_csrf)
                await client.post("/api/v2/auth/logout", headers=self.csrf(other_csrf))
                admin_csrf = await self.login(client, "security-admin", "AdminPassword!123")
                await self._assert_owner_ids_are_hidden(client, admin_csrf)
        self.assertEqual(await self.scalar("SELECT display_name FROM contacts WHERE id='contact_owner'"), "Secret Contact")
        self.assertIsNone(await self.scalar("SELECT read_at FROM notification_events WHERE id='notification_owner'"))
        self.assertEqual(await self.scalar("SELECT status FROM mail_operations WHERE id='operation_owner'"), "pending")

    async def test_csrf_origin_cursor_token_search_and_upload_inputs_fail_safely(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.161") as client:
                csrf = await self.login(client, "security-owner", "OwnerPassword!123")
                missing_csrf = await client.put("/api/v2/settings", json={"ui_preferences": {"theme": "dark"}})
                wrong_origin = await client.put(
                    "/api/v2/settings",
                    headers=self.csrf(csrf, "https://evil.example"),
                    json={"ui_preferences": {"theme": "dark"}},
                )
                forged_cursor = await client.get("/api/v2/threads", params={"cursor": "forged.cursor"})
                forged_delete = await client.post(
                    "/api/v2/operations",
                    headers=self.csrf(csrf),
                    json={
                        "target_type": "remote_instance",
                        "target_id": "remote_owner",
                        "operation_type": "delete_permanent",
                        "desired_state": {},
                        "idempotency_key": "forged-delete",
                        "confirmation_token": "forged.token",
                    },
                )
                sql_value = "x' OR 1=1 --"
                search = await client.post(
                    "/api/v2/search",
                    headers=self.csrf(csrf),
                    json={"filters": {"keyword": sql_value}, "limit": 20},
                )
                oversized = await client.post(
                    "/api/v2/profile/avatar",
                    headers={**self.csrf(csrf), "Content-Type": "image/png"},
                    content=b"x" * (5 * 1024 * 1024 + 1),
                )
                active_svg = await client.post(
                    "/api/v2/profile/avatar",
                    headers={**self.csrf(csrf), "Content-Type": "image/svg+xml"},
                    content=b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
                )
        self.assertIn(missing_csrf.status_code, {401, 403})
        self.assertEqual(wrong_origin.status_code, 403)
        self.assertEqual(forged_cursor.status_code, 400)
        self.assertIn(forged_delete.status_code, {400, 409})
        self.assertLess(search.status_code, 500)
        self.assertNotIn(sql_value, json.dumps(search.json(), ensure_ascii=False))
        self.assertEqual(oversized.status_code, 422)
        self.assertEqual(active_svg.status_code, 422)
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM mail_operations WHERE idempotency_key='forged-delete'"), 0)

    async def test_old_session_is_rejected_after_password_change(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.162") as client:
                csrf = await self.login(client, "security-owner", "OwnerPassword!123")
                old_cookie = client.cookies.get(SESSION_COOKIE_NAME)
                changed = await client.post(
                    "/api/v2/auth/password",
                    headers=self.csrf(csrf),
                    json={
                        "current_password": "OwnerPassword!123",
                        "new_password": "OwnerPassword!456",
                    },
                )
                self.assertEqual(changed.status_code, 200, changed.text)
                client.cookies.clear()
                client.cookies.set(SESSION_COOKIE_NAME, old_cookie, domain="testserver", path="/")
                replay = await client.get("/api/v2/auth/me")
        self.assertEqual(replay.status_code, 401)


if __name__ == "__main__":
    import unittest
    unittest.main()
