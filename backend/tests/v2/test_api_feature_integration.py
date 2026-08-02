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
from flymail.repositories.users import UserRepository
from tests.v2.mysql_test_case import MySqlIsolatedAsyncioTestCase
from flymail.api.app import create_app


ORIGIN = "https://testserver"


class ApiFeatureIntegrationTests(MySqlIsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await run_migrations(self.api_pool)
        await self._clear_tables()
        self.temp_dir = tempfile.TemporaryDirectory(prefix="flymail-v2-feature-e2e-")
        root = Path(self.temp_dir.name)
        self.settings = FlyMailSettings(
            role="api",
            database_url=self.database_url(),
            data_dir=root,
            object_dir=root / "objects" / "sha256",
            object_tmp_dir=root / "objects" / ".tmp",
            session_secret="feature-e2e-session-secret-0123456789abcdef",
            db_pool_name="flymail-api",
            db_min_connections=2,
            db_max_connections=12,
        )
        self.user = await self._create_user("feature-user", "FeaturePassword!123")
        await self._seed_identity()

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()
        await super().asyncTearDown()

    async def _clear_tables(self) -> None:
        tables = (
            "realtime_events", "notification_preferences", "notification_events",
            "send_attempts", "draft_versions", "draft_attachments", "draft_recipients",
            "drafts", "job_attempts", "worker_jobs", "mail_operations", "outbox_events",
            "contacts", "account_runtime_state", "mail_identities", "mail_accounts",
            "content_references", "content_objects", "audit_events", "login_rate_limits",
            "user_sessions", "user_profiles", "user_settings", "users", "process_heartbeats",
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
                AdminContext("usr_feature_e2e_admin"),
                username=username,
                password_hash=hash_password(password),
            )
            await connection.commit()
        return user

    async def _seed_identity(self) -> None:
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO mail_accounts (
                        id, user_uid, provider_key, email, normalized_email,
                        display_name, status, poll_interval_seconds, created_at, updated_at
                    ) VALUES ('acc_feature', %s, 'gmail', 'feature@example.com',
                              'feature@example.com', 'Feature account', 'active', 300, 1, 1)
                    """,
                    (self.user.id,),
                )
                await cursor.execute(
                    """
                    INSERT INTO mail_identities (
                        id, user_uid, account_id, from_address,
                        normalized_from_address, display_name, is_default,
                        is_verified, created_at, updated_at
                    ) VALUES ('ident_feature', %s, 'acc_feature',
                              'feature@example.com', 'feature@example.com',
                              'Feature Sender', 1, 1, 1, 1)
                    """,
                    (self.user.id,),
                )
                await cursor.execute(
                    """
                    INSERT INTO account_runtime_state (
                        account_id, user_uid, status, idle_status,
                        last_activity_at, last_change_at, next_reconcile_at,
                        failure_count, backoff_until, updated_at
                    ) VALUES ('acc_feature', %s, 'normal', 'disconnected',
                              0, 0, 0, 0, 0, 0)
                    """,
                    (self.user.id,),
                )
            await connection.commit()

    @asynccontextmanager
    async def running_app(self):
        app = create_app(self.settings)
        async with app.router.lifespan_context(app):
            app.state.now_fn = lambda: 100.0
            app.state.realtime_service.now_fn = lambda: 100.0
            app.state.settings_contacts_service.now_fn = lambda: 100.0
            app.state.compose_service.now_fn = lambda: 100.0
            yield app

    def client(self, app) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=app,
                raise_app_exceptions=False,
                client=("203.0.113.150", 443),
            ),
            base_url=ORIGIN,
        )

    @staticmethod
    def csrf_headers(token: str) -> dict[str, str]:
        return {"Origin": ORIGIN, "X-CSRF-Token": token}

    async def test_authenticated_vertical_flow_stays_local_and_secret_free(self):
        async with self.running_app() as app:
            async with self.client(app) as client:
                login = await client.post(
                    "/api/v2/auth/login",
                    json={"username": "feature-user", "password": "FeaturePassword!123"},
                )
                self.assertEqual(login.status_code, 200)
                csrf = str(login.json()["csrf_token"])
                bootstrap = await client.get("/api/v2/bootstrap")
                settings = await client.put(
                    "/api/v2/settings",
                    headers=self.csrf_headers(csrf),
                    json={
                        "ui_preferences": {"theme": "dark", "density": "compact"},
                        "compose_preferences": {"autosave_seconds": 10},
                        "remote_image_policy": {"default": "block"},
                    },
                )
                contact = await client.post(
                    "/api/v2/contacts",
                    headers=self.csrf_headers(csrf),
                    json={
                        "display_name": "Recipient",
                        "primary_email": "recipient@example.net",
                        "emails": ["recipient@example.net"],
                    },
                )
                draft = await client.post(
                    "/api/v2/drafts",
                    headers=self.csrf_headers(csrf),
                    json={
                        "account_id": "acc_feature",
                        "identity_id": "ident_feature",
                        "subject": "Vertical flow",
                        "body_html": "<p>Local body</p>",
                        "body_text": "Local body",
                        "recipients": {
                            "to": [{"address": "recipient@example.net"}],
                            "cc": [],
                            "bcc": [{"address": "hidden@example.net"}],
                        },
                    },
                )
                send = await client.post(
                    f"/api/v2/drafts/{draft.json()['id']}/send",
                    headers=self.csrf_headers(csrf),
                    json={"idempotency_key": "feature-e2e-send"},
                )
                events = await client.get("/api/v2/events", params={"after": 0})
                notification_settings = await client.get("/api/v2/notification-settings")
                version = await client.get("/api/v2/version")

        for response in (
            bootstrap,
            settings,
            contact,
            draft,
            send,
            events,
            notification_settings,
            version,
        ):
            self.assertLess(response.status_code, 300, response.text)
            self.assertIn("X-Request-ID", response.headers)
            self.assertIn("Server-Timing", response.headers)

        self.assertEqual(bootstrap.json()["accounts"][0]["id"], "acc_feature")
        self.assertEqual(settings.json()["ui_preferences"]["theme"], "dark")
        self.assertEqual(contact.json()["primary_email"], "recipient@example.net")
        self.assertEqual(draft.json()["version"], 1)
        self.assertEqual(send.status_code, 202)
        self.assertIn("settings.updated", [item["event_type"] for item in events.json()["events"]])
        self.assertEqual(
            await self.scalar("SELECT COUNT(*) FROM worker_jobs WHERE job_kind='send.deliver'"),
            1,
        )
        self.assertEqual(
            await self.scalar("SELECT COUNT(*) FROM send_attempts"),
            0,
        )
        job_payload = await self.scalar(
            "SELECT payload FROM worker_jobs WHERE id=%s",
            (send.json()["job_id"],),
        )
        rendered = json.dumps(
            {
                "bootstrap": bootstrap.json(),
                "send": send.json(),
                "events": events.json(),
                "job_payload": job_payload,
            },
            ensure_ascii=False,
            default=str,
        ).casefold()
        for forbidden in (
            "featurepassword!123",
            "hidden@example.net",
            "access_token",
            "refresh_token",
            "ciphertext",
            self.settings.session_secret.casefold(),
            self.settings.database_url.casefold(),
        ):
            self.assertNotIn(forbidden, rendered)
