from __future__ import annotations

import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

import httpx

from flymail.config import FlyMailSettings
from flymail.infrastructure.db.migrations.runner import run_migrations
from flymail.infrastructure.security.passwords import hash_password
from flymail.repositories.base import AdminContext
from flymail.repositories.users import UserRepository
from tests.v2.mysql_test_case import MySqlIsolatedAsyncioTestCase
from flymail.api.app import create_app


ORIGIN = "https://testserver"


class NotificationApiTests(MySqlIsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await run_migrations(self.api_pool)
        await self._clear_tables()
        self.temp_dir = tempfile.TemporaryDirectory(prefix="flymail-v2-notification-api-")
        root = Path(self.temp_dir.name)
        self.settings = FlyMailSettings(
            role="api",
            database_url=self.database_url(),
            data_dir=root,
            object_dir=root / "objects" / "sha256",
            object_tmp_dir=root / "objects" / ".tmp",
            session_secret="notification-api-session-secret-0123456789abcdef",
            db_pool_name="flymail-api",
            db_min_connections=2,
            db_max_connections=12,
        )
        self.user = await self._create_user("notification-user", "NotificationPassword!123")
        self.other = await self._create_user("notification-other", "OtherPassword!123")
        await self._seed_notifications()

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()
        await super().asyncTearDown()

    async def _clear_tables(self) -> None:
        tables = (
            "realtime_events", "notification_preferences", "notification_deliveries",
            "notification_events", "login_rate_limits", "user_sessions",
            "user_profiles", "user_settings", "users", "process_heartbeats",
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
                AdminContext("usr_notification_api_admin"),
                username=username,
                password_hash=hash_password(password),
            )
            await connection.commit()
        return user

    async def _seed_notifications(self) -> None:
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.executemany(
                    """
                    INSERT INTO notification_events (
                        id, user_uid, event_type, title, summary,
                        action_path, account_id, dedupe_key,
                        created_at, read_at, dismissed_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, NULL, %s, %s, %s, NULL)
                    """,
                    (
                        ("notify_api_1", self.user.id, "new_mail", "Mail one", "Summary one", "/mail/1", "dedupe-1", 10.0, None),
                        ("notify_api_2", self.user.id, "send_failed", "Mail two", "Summary two", "/mail/2", "dedupe-2", 20.0, None),
                        ("notify_api_3", self.user.id, "sync_failed", "Mail three", "Summary three", "/mail/3", "dedupe-3", 30.0, 31.0),
                        ("notify_api_other", self.other.id, "new_mail", "Other secret", "Other summary", "/mail/secret", "dedupe-other", 99.0, None),
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

    async def login(self, client: httpx.AsyncClient) -> str:
        response = await client.post(
            "/api/v2/auth/login",
            json={"username": "notification-user", "password": "NotificationPassword!123"},
        )
        self.assertEqual(response.status_code, 200)
        return str(response.json()["csrf_token"])

    @staticmethod
    def csrf_headers(token: str) -> dict[str, str]:
        return {"Origin": ORIGIN, "X-CSRF-Token": token}

    async def test_notification_list_cursor_and_unread_count_are_tenant_scoped(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.130") as client:
                await self.login(client)
                first = await client.get("/api/v2/notifications", params={"limit": 1})
                second = await client.get(
                    "/api/v2/notifications",
                    params={"limit": 1, "cursor": first.json()["next_cursor"]},
                )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["items"][0]["id"], "notify_api_3")
        self.assertEqual(first.json()["unread_count"], 2)
        self.assertEqual(second.json()["items"][0]["id"], "notify_api_2")
        self.assertNotIn("Other secret", first.text + second.text)
        self.assertEqual(first.headers["cache-control"], "no-store")

    async def test_read_dismiss_and_read_all_require_csrf_and_publish_realtime(self):
        async with self.running_app() as app:
            app.state.realtime_service.now_fn = lambda: 100.0
            async with self.client(app, "203.0.113.131") as client:
                csrf = await self.login(client)
                denied = await client.post("/api/v2/notifications/notify_api_1/read")
                read = await client.post(
                    "/api/v2/notifications/notify_api_1/read",
                    headers=self.csrf_headers(csrf),
                )
                dismissed = await client.post(
                    "/api/v2/notifications/notify_api_2/dismiss",
                    headers=self.csrf_headers(csrf),
                )
                all_read = await client.post(
                    "/api/v2/notifications/read-all",
                    headers=self.csrf_headers(csrf),
                )
                events = await client.get("/api/v2/events", params={"after": 0})
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(read.status_code, 200)
        self.assertEqual(read.json()["read"], True)
        self.assertEqual(dismissed.status_code, 204)
        self.assertEqual(all_read.status_code, 200)
        self.assertEqual(all_read.json()["updated_count"], 0)
        self.assertIn(
            "notification.updated",
            [item["event_type"] for item in events.json()["events"]],
        )
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM notification_events WHERE user_uid=%s AND read_at IS NULL AND dismissed_at IS NULL",
                (self.user.id,),
            ),
            0,
        )

    async def test_safe_notification_preferences_round_trip_without_channel_secrets(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.132") as client:
                csrf = await self.login(client)
                defaults = await client.get("/api/v2/notification-settings")
                invalid = await client.put(
                    "/api/v2/notification-settings",
                    headers=self.csrf_headers(csrf),
                    json={"quiet_hours": {"start": "25:00", "end": "08:00"}},
                )
                updated = await client.put(
                    "/api/v2/notification-settings",
                    headers=self.csrf_headers(csrf),
                    json={
                        "in_app_enabled": True,
                        "external_enabled": False,
                        "include_images": True,
                        "quiet_hours": {"start": "22:30", "end": "07:30"},
                        "event_preferences": {
                            "new_mail": True,
                            "send_failed": True,
                            "sync_failed": False,
                        },
                    },
                )
                loaded = await client.get("/api/v2/notification-settings")
        self.assertEqual(defaults.status_code, 200)
        self.assertEqual(defaults.json()["in_app_enabled"], True)
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(loaded.json()["include_images"], True)
        rendered = loaded.text.casefold()
        for forbidden in ("token", "password", "secret", "ciphertext", "webhook_url"):
            self.assertNotIn(forbidden, rendered)

    async def test_cross_tenant_notification_ids_are_indistinguishable_from_missing(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.133") as client:
                csrf = await self.login(client)
                missing = await client.post(
                    "/api/v2/notifications/does-not-exist/read",
                    headers=self.csrf_headers(csrf),
                )
                cross = await client.post(
                    "/api/v2/notifications/notify_api_other/read",
                    headers=self.csrf_headers(csrf),
                )
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(cross.status_code, 404)
        self.assertEqual(missing.json()["error"]["code"], cross.json()["error"]["code"])
