from __future__ import annotations

import json
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

import aiomysql
import httpx

from flymail.application.bootstrap import BootstrapResponse
from flymail.config import FlyMailSettings
from flymail.infrastructure.db.migrations.runner import run_migrations
from flymail.infrastructure.security.credentials import CredentialCipher
from flymail.infrastructure.security.passwords import hash_password
from flymail.repositories.accounts import AccountRepository, CredentialRepository
from flymail.repositories.base import AdminContext, TenantContext
from flymail.repositories.mailboxes import MailboxRepository
from flymail.repositories.users import UserRepository
from tests.v2.mysql_test_case import MySqlIsolatedAsyncioTestCase
from v2_dev import create_app


ORIGIN = "https://testserver"


class BootstrapApiTests(MySqlIsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await run_migrations(self.api_pool)
        await self._clear_tables()
        self.temp_dir = tempfile.TemporaryDirectory(prefix="flymail-v2-bootstrap-api-")
        root = Path(self.temp_dir.name)
        self.settings = FlyMailSettings(
            role="api",
            database_url=self.database_url(),
            data_dir=root,
            object_dir=root / "objects" / "sha256",
            object_tmp_dir=root / "objects" / ".tmp",
            session_secret="bootstrap-api-session-secret-0123456789abcdef",
            db_pool_name="flymail-api",
            db_min_connections=2,
            db_max_connections=12,
        )
        self.user = await self._create_user("bootstrap-user", "BootstrapPassword!123")
        self.other = await self._create_user("bootstrap-other", "OtherPassword!123")
        await self._seed_bootstrap_data()

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()
        await super().asyncTearDown()

    async def _clear_tables(self) -> None:
        tables = (
            "notification_deliveries",
            "notification_events",
            "realtime_events",
            "message_body_parts",
            "message_memberships",
            "message_remote_instances",
            "messages",
            "threads",
            "mailboxes",
            "account_runtime_state",
            "provider_credentials",
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
                AdminContext("usr_bootstrap_test_admin"),
                username=username,
                password_hash=hash_password(password),
            )
            await connection.commit()
        return user

    async def _seed_bootstrap_data(self) -> None:
        tenant = TenantContext(self.user.id)
        other_tenant = TenantContext(self.other.id)
        async with self.api_pool.acquire() as connection:
            await connection.begin()
            accounts = AccountRepository(connection)
            mailboxes = MailboxRepository(connection)

            self.active_account = await accounts.create_account(
                tenant,
                provider_key="gmail",
                email="active@example.com",
                display_name="Active mailbox",
                status="active",
            )
            self.disabled_account = await accounts.create_account(
                tenant,
                provider_key="outlook",
                email="disabled@example.com",
                display_name="Disabled mailbox",
                status="disabled",
            )
            self.auth_account = await accounts.create_account(
                tenant,
                provider_key="qq",
                email="auth@example.com",
                display_name="Authorization required",
                status="auth_required",
            )
            self.pending_account = await accounts.create_account(
                tenant,
                provider_key="netease",
                email="pending@example.com",
                display_name="Pending mailbox",
                status="pending",
            )
            self.other_account = await accounts.create_account(
                other_tenant,
                provider_key="gmail",
                email="other@example.com",
                display_name="Other mailbox",
                status="active",
            )

            await accounts.ensure_runtime_state(tenant, self.active_account.id, status="degraded")
            await accounts.ensure_runtime_state(tenant, self.disabled_account.id, status="disabled")
            await accounts.ensure_runtime_state(tenant, self.auth_account.id, status="auth_required")
            await accounts.ensure_runtime_state(tenant, self.pending_account.id, status="normal")
            await accounts.ensure_runtime_state(other_tenant, self.other_account.id, status="normal")

            self.active_inbox = await mailboxes.upsert_mailbox(
                tenant,
                account_id=self.active_account.id,
                native_key="INBOX",
                native_name="Inbox",
                semantic_key="inbox",
                mailbox_type="folder",
            )
            self.active_sent = await mailboxes.upsert_mailbox(
                tenant,
                account_id=self.active_account.id,
                native_key="[Gmail]/Sent Mail",
                native_name="Sent Mail",
                semantic_key="sent",
                mailbox_type="folder",
            )
            self.active_label = await mailboxes.upsert_mailbox(
                tenant,
                account_id=self.active_account.id,
                native_key="STARRED",
                native_name="Starred",
                semantic_key="important",
                mailbox_type="label",
            )
            self.disabled_inbox = await mailboxes.upsert_mailbox(
                tenant,
                account_id=self.disabled_account.id,
                native_key="INBOX",
                native_name="Inbox",
                semantic_key="inbox",
                mailbox_type="folder",
            )
            self.other_inbox = await mailboxes.upsert_mailbox(
                other_tenant,
                account_id=self.other_account.id,
                native_key="INBOX",
                native_name="Inbox",
                semantic_key="inbox",
                mailbox_type="folder",
            )

            cipher = CredentialCipher.from_master_secret(self.settings.session_secret)
            await CredentialRepository(connection).store_encrypted(
                tenant,
                self.active_account.id,
                credential_type="oauth",
                value=cipher.encrypt(
                    self.active_account.id,
                    b"bootstrap-credential-secret",
                ),
            )

            async with connection.cursor() as cursor:
                await cursor.executemany(
                    "UPDATE mailboxes SET total_count=%s, unread_count=%s WHERE id=%s",
                    (
                        (15, 4, self.active_inbox.id),
                        (7, 0, self.active_sent.id),
                        (3, 2, self.active_label.id),
                        (9, 9, self.disabled_inbox.id),
                        (80, 50, self.other_inbox.id),
                    ),
                )
                await cursor.execute(
                    """
                    UPDATE account_runtime_state
                    SET last_error_class='temporary',
                        last_error_message='bootstrap-runtime-error-secret',
                        failure_count=3
                    WHERE account_id=%s
                    """,
                    (self.active_account.id,),
                )
                await cursor.execute(
                    "UPDATE user_profiles SET nickname='Bootstrap Nickname' WHERE user_uid=%s",
                    (self.user.id,),
                )
                await cursor.execute(
                    """
                    UPDATE user_settings
                    SET ui_preferences=JSON_OBJECT('theme', 'dark', 'density', 'compact')
                    WHERE user_uid=%s
                    """,
                    (self.user.id,),
                )
                await cursor.execute(
                    """
                    INSERT INTO messages (
                        id, user_uid, canonical_message_key, subject, snippet,
                        body_state, search_state, created_at, updated_at
                    ) VALUES (
                        'msg_bootstrap_secret', %s, 'bootstrap-secret-message',
                        'Bootstrap private message', 'bootstrap-message-body-secret',
                        'ready', 'ready', 1, 1
                    )
                    """,
                    (self.user.id,),
                )
                await cursor.executemany(
                    """
                    INSERT INTO notification_events (
                        id, user_uid, event_type, title, summary, action_path,
                        account_id, dedupe_key, created_at, read_at, dismissed_at
                    ) VALUES (%s, %s, 'sync.alert', %s, %s, '', %s, %s, %s, %s, NULL)
                    """,
                    (
                        (
                            "ntf_bootstrap_1",
                            self.user.id,
                            "bootstrap-notification-detail-secret",
                            "private notification summary",
                            self.active_account.id,
                            "bootstrap-notification-1",
                            10.0,
                            None,
                        ),
                        (
                            "ntf_bootstrap_2",
                            self.user.id,
                            "Second unread",
                            "second private notification",
                            self.auth_account.id,
                            "bootstrap-notification-2",
                            11.0,
                            None,
                        ),
                        (
                            "ntf_bootstrap_read",
                            self.user.id,
                            "Already read",
                            "read notification",
                            self.active_account.id,
                            "bootstrap-notification-read",
                            12.0,
                            12.5,
                        ),
                        (
                            "ntf_bootstrap_other",
                            self.other.id,
                            "Other user notification",
                            "other notification summary",
                            self.other_account.id,
                            "bootstrap-notification-other",
                            13.0,
                            None,
                        ),
                    ),
                )
                await cursor.execute(
                    """
                    INSERT INTO realtime_events (
                        event_id, user_uid, event_type, aggregate_type,
                        aggregate_id, payload, created_at, expires_at
                    ) VALUES ('evt_bootstrap_user', %s, 'mail.updated', 'account', %s, '{}', 20, 200)
                    """,
                    (self.user.id, self.active_account.id),
                )
                self.user_realtime_cursor = int(cursor.lastrowid)
                await cursor.execute(
                    """
                    INSERT INTO realtime_events (
                        event_id, user_uid, event_type, aggregate_type,
                        aggregate_id, payload, created_at, expires_at
                    ) VALUES ('evt_bootstrap_other', %s, 'mail.updated', 'account', %s, '{}', 21, 200)
                    """,
                    (self.other.id, self.other_account.id),
                )
                self.other_realtime_cursor = int(cursor.lastrowid)
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

    def test_bootstrap_response_is_immutable(self):
        self.assertTrue(BootstrapResponse.model_config.get("frozen"))

    async def test_bootstrap_requires_authentication(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.60") as client:
                response = await client.get("/api/v2/bootstrap")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "authentication_required")

    async def test_bootstrap_is_bounded_secret_free_and_uses_semantic_navigation(self):
        recorded_queries: list[str] = []
        original_execute = aiomysql.cursors.Cursor.execute

        async def counted_execute(cursor, query, args=None):
            recorded_queries.append(" ".join(str(query).split()).casefold())
            return await original_execute(cursor, query, args)

        async with self.running_app() as app:
            async with self.client(app, "203.0.113.61") as client:
                csrf_token = await self.login(
                    client,
                    "bootstrap-user",
                    "BootstrapPassword!123",
                )
                with patch.object(
                    aiomysql.cursors.Cursor,
                    "execute",
                    new=counted_execute,
                ):
                    response = await client.get("/api/v2/bootstrap")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("cache-control"), "no-store")
        self.assertNotIn("etag", response.headers)
        self.assertLessEqual(len(recorded_queries), 6, recorded_queries)

        payload = response.json()
        self.assertEqual(
            set(payload),
            {
                "user",
                "permissions",
                "accounts",
                "navigation",
                "ui_preferences",
                "sync_alert_summary",
                "csrf_token",
                "realtime_cursor",
                "version",
            },
        )
        self.assertEqual(
            payload["user"],
            {
                "id": self.user.id,
                "username": "bootstrap-user",
                "role": "user",
                "enabled": True,
                "nickname": "Bootstrap Nickname",
                "avatar_object_sha256": None,
            },
        )
        self.assertEqual(
            payload["permissions"],
            ["accounts.manage", "mail.read", "mail.send", "settings.manage"],
        )
        self.assertEqual(payload["csrf_token"], csrf_token)
        self.assertEqual(payload["realtime_cursor"], self.user_realtime_cursor)
        self.assertEqual(payload["ui_preferences"], {"theme": "dark", "density": "compact"})
        self.assertEqual(
            payload["sync_alert_summary"],
            {
                "auth_required_accounts": 1,
                "degraded_accounts": 1,
                "pending_accounts": 1,
                "unread_notifications": 2,
            },
        )

        account_by_id = {item["id"]: item for item in payload["accounts"]}
        self.assertEqual(set(account_by_id), {
            self.active_account.id,
            self.disabled_account.id,
            self.auth_account.id,
            self.pending_account.id,
        })
        self.assertTrue(account_by_id[self.active_account.id]["include_in_unified"])
        self.assertFalse(account_by_id[self.disabled_account.id]["include_in_unified"])
        self.assertFalse(account_by_id[self.auth_account.id]["include_in_unified"])
        self.assertFalse(account_by_id[self.pending_account.id]["include_in_unified"])
        self.assertEqual(account_by_id[self.disabled_account.id]["status"], "disabled")
        self.assertEqual(account_by_id[self.active_account.id]["total_count"], 22)
        self.assertEqual(account_by_id[self.active_account.id]["unread_count"], 4)

        unified = payload["navigation"]["unified"]
        self.assertEqual(unified["account_ids"], [self.active_account.id])
        self.assertEqual(unified["total_count"], 15)
        self.assertEqual(unified["unread_count"], 4)

        navigation_by_account = {
            item["account_id"]: item for item in payload["navigation"]["accounts"]
        }
        active_navigation = navigation_by_account[self.active_account.id]
        self.assertEqual(
            [item["semantic_key"] for item in active_navigation["semantic_mailboxes"]],
            ["inbox", "sent"],
        )
        self.assertEqual(
            [item["native_key"] for item in active_navigation["native_labels"]],
            ["STARRED"],
        )
        self.assertEqual(active_navigation["native_labels"][0]["semantic_key"], "important")

        rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        for forbidden in (
            "bootstrap-credential-secret",
            "bootstrap-message-body-secret",
            "bootstrap-notification-detail-secret",
            "bootstrap-runtime-error-secret",
            "ciphertext",
            "credential_type",
            "endpoint_config",
            "last_error_message",
            "notification_events",
            "sync_history",
        ):
            self.assertNotIn(forbidden, rendered)
        query_text = "\n".join(recorded_queries)
        self.assertNotIn(" from messages ", query_text)
        self.assertNotIn("provider_credentials", query_text)
        self.assertNotIn("notification_events.title", query_text)
        self.assertNotIn("notification_events.summary", query_text)

    async def test_bootstrap_realtime_cursor_and_accounts_are_tenant_scoped(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.62") as client:
                await self.login(client, "bootstrap-other", "OtherPassword!123")
                response = await client.get("/api/v2/bootstrap")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["user"]["id"], self.other.id)
        self.assertEqual([item["id"] for item in payload["accounts"]], [self.other_account.id])
        self.assertEqual(payload["navigation"]["unified"]["account_ids"], [self.other_account.id])
        self.assertEqual(payload["navigation"]["unified"]["total_count"], 80)
        self.assertEqual(payload["navigation"]["unified"]["unread_count"], 50)
        self.assertEqual(payload["realtime_cursor"], self.other_realtime_cursor)
        self.assertEqual(payload["sync_alert_summary"]["unread_notifications"], 1)
        rendered = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn(self.active_account.id, rendered)
        self.assertNotIn("active@example.com", rendered)
