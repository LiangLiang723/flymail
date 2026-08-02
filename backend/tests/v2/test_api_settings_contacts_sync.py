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


class SettingsContactsSyncApiTests(MySqlIsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await run_migrations(self.api_pool)
        await self._clear_tables()
        self.temp_dir = tempfile.TemporaryDirectory(prefix="flymail-v2-settings-api-")
        root = Path(self.temp_dir.name)
        self.settings = FlyMailSettings(
            role="api",
            database_url=self.database_url(),
            data_dir=root,
            object_dir=root / "objects" / "sha256",
            object_tmp_dir=root / "objects" / ".tmp",
            session_secret="settings-api-session-secret-0123456789abcdef",
            db_pool_name="flymail-api",
            db_min_connections=2,
            db_max_connections=12,
        )
        self.admin = await self._create_user("settings-admin", "AdminPassword!123", role="admin")
        self.user = await self._create_user("settings-user", "UserPassword!123")
        self.other = await self._create_user("settings-other", "OtherPassword!123")
        await self._seed_jobs()

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()
        await super().asyncTearDown()

    async def _clear_tables(self) -> None:
        tables = (
            "realtime_events", "audit_events", "job_attempts", "worker_jobs",
            "sync_cursors", "account_runtime_state", "contacts", "mail_accounts",
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
                AdminContext("usr_settings_test_admin"),
                username=username,
                password_hash=hash_password(password),
                role=role,
            )
            await connection.commit()
        return user

    async def _seed_jobs(self) -> None:
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO mail_accounts (
                        id, user_uid, provider_key, email, normalized_email,
                        display_name, status, poll_interval_seconds, created_at, updated_at
                    ) VALUES ('acc_settings_sync', %s, 'gmail', 'sync@example.com',
                              'sync@example.com', 'Sync', 'active', 300, 1, 1)
                    """,
                    (self.user.id,),
                )
                await cursor.execute(
                    """
                    INSERT INTO account_runtime_state (
                        account_id, user_uid, status, idle_status,
                        last_activity_at, last_change_at, next_reconcile_at,
                        failure_count, backoff_until, updated_at
                    ) VALUES ('acc_settings_sync', %s, 'normal', 'disconnected',
                              0, 0, 0, 0, 0, 0)
                    """,
                    (self.user.id,),
                )
                await cursor.execute(
                    """
                    INSERT INTO sync_cursors (
                        id, user_uid, account_id, mailbox_id, phase,
                        cursor_type, cursor_json, last_uid, highest_modseq, updated_at
                    ) VALUES ('cursor_settings_sync', %s, 'acc_settings_sync', '',
                              'history', 'json', '{"page":3}', 42, 99, 1)
                    """,
                    (self.user.id,),
                )
            job_id = await JobRepository(connection).enqueue(
                JobSpec(
                    queue_name="history",
                    job_kind="sync.initial",
                    payload={"account_id": "acc_settings_sync", "phase": "history"},
                    user_uid=self.user.id,
                    account_id="acc_settings_sync",
                    provider_key="gmail",
                    priority=500,
                    available_at=0,
                    max_attempts=20,
                    dedupe_key="settings-history-job",
                ),
                now=1,
            )
            await connection.commit()
        self.history_job_id = job_id

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

    async def test_settings_update_is_validated_scoped_and_publishes_invalidation(self):
        async with self.running_app() as app:
            app.state.realtime_service.now_fn = lambda: 100.0
            async with self.client(app, "203.0.113.120") as client:
                csrf = await self.login(client, "settings-user", "UserPassword!123")
                invalid = await client.put(
                    "/api/v2/settings",
                    headers=self.csrf_headers(csrf),
                    json={"ui_preferences": {"theme": "neon", "density": "compact"}},
                )
                updated = await client.put(
                    "/api/v2/settings",
                    headers=self.csrf_headers(csrf),
                    json={
                        "body_cache_quota_bytes": 104857600,
                        "attachment_cache_quota_bytes": 209715200,
                        "ui_preferences": {"theme": "dark", "density": "compact"},
                        "compose_preferences": {"autosave_seconds": 10},
                        "remote_image_policy": {"default": "block"},
                    },
                )
                loaded = await client.get("/api/v2/settings")
                events = await client.get("/api/v2/events", params={"after": 0})
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(loaded.json()["ui_preferences"]["theme"], "dark")
        self.assertEqual(loaded.json()["body_cache_quota_bytes"], 104857600)
        self.assertIn("settings.updated", [item["event_type"] for item in events.json()["events"]])
        self.assertNotIn("settings-other", json.dumps(loaded.json()))

    async def test_contact_crud_and_suggestions_never_cross_tenants(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.121") as client:
                csrf = await self.login(client, "settings-user", "UserPassword!123")
                created = await client.post(
                    "/api/v2/contacts",
                    headers=self.csrf_headers(csrf),
                    json={
                        "display_name": "Alice Team",
                        "primary_email": "Alice@Example.com",
                        "emails": ["alice@example.com", "alias@example.com"],
                    },
                )
                duplicate = await client.post(
                    "/api/v2/contacts",
                    headers=self.csrf_headers(csrf),
                    json={
                        "display_name": "Duplicate",
                        "primary_email": "alice@example.COM",
                        "emails": ["alice@example.com"],
                    },
                )
                listing = await client.get("/api/v2/contacts", params={"q": "ali"})
                updated = await client.patch(
                    f"/api/v2/contacts/{created.json()['id']}",
                    headers=self.csrf_headers(csrf),
                    json={"display_name": "Alice Updated"},
                )
                deleted = await client.delete(
                    f"/api/v2/contacts/{created.json()['id']}",
                    headers=self.csrf_headers(csrf),
                )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual([item["id"] for item in listing.json()["items"]], [created.json()["id"]])
        self.assertEqual(updated.json()["display_name"], "Alice Updated")
        self.assertEqual(deleted.status_code, 204)

    async def test_normal_user_cannot_access_admin_sync_controls(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.122") as client:
                csrf = await self.login(client, "settings-user", "UserPassword!123")
                listing = await client.get("/api/v2/admin/history-sync")
                paused = await client.post(
                    f"/api/v2/admin/history-sync/{self.history_job_id}/pause",
                    headers=self.csrf_headers(csrf),
                )
        self.assertEqual(listing.status_code, 403)
        self.assertEqual(paused.status_code, 403)

    async def test_admin_history_sync_pause_resume_retry_preserves_payload_and_cursor(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.123") as client:
                csrf = await self.login(client, "settings-admin", "AdminPassword!123")
                listing = await client.get("/api/v2/admin/history-sync")
                paused = await client.post(
                    f"/api/v2/admin/history-sync/{self.history_job_id}/pause",
                    headers=self.csrf_headers(csrf),
                )
                resumed = await client.post(
                    f"/api/v2/admin/history-sync/{self.history_job_id}/resume",
                    headers=self.csrf_headers(csrf),
                )
                async with self.api_pool.acquire() as connection:
                    async with connection.cursor() as cursor:
                        await cursor.execute(
                            "UPDATE worker_jobs SET status='failed', last_error_class='ProviderError', last_error_message='safe failure' WHERE id=%s",
                            (self.history_job_id,),
                        )
                    await connection.commit()
                retried = await client.post(
                    f"/api/v2/admin/history-sync/{self.history_job_id}/retry",
                    headers=self.csrf_headers(csrf),
                )
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()["items"][0]["cursor"]["last_uid"], 42)
        self.assertEqual(paused.json()["status"], "paused")
        self.assertEqual(resumed.json()["status"], "pending")
        self.assertEqual(retried.json()["status"], "pending")
        payload = await self.scalar("SELECT payload FROM worker_jobs WHERE id=%s", (self.history_job_id,))
        decoded = json.loads(payload) if isinstance(payload, str) else payload
        self.assertEqual(decoded["account_id"], "acc_settings_sync")
        self.assertEqual(
            await self.scalar("SELECT last_uid FROM sync_cursors WHERE id='cursor_settings_sync'"),
            42,
        )
        self.assertGreaterEqual(
            int(await self.scalar("SELECT COUNT(*) FROM audit_events WHERE actor_user_uid=%s", (self.admin.id,))),
            3,
        )
