from __future__ import annotations

import io
import json
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from PIL import Image

from flymail.config import FlyMailSettings
from flymail.infrastructure.db.migrations.runner import run_migrations
from flymail.infrastructure.security.passwords import hash_password
from flymail.repositories.base import AdminContext
from flymail.repositories.users import UserRepository
from tests.v2.mysql_test_case import MySqlIsolatedAsyncioTestCase
from flymail.api.app import create_app


ORIGIN = "https://testserver"


def png_bytes(width: int = 420, height: int = 180) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (width, height), (32, 96, 160)).save(output, format="PNG")
    return output.getvalue()


class PersonalNotificationApiTests(MySqlIsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await run_migrations(self.api_pool)
        await self._clear_tables()
        self.temp_dir = tempfile.TemporaryDirectory(prefix="flymail-v2-personal-")
        root = Path(self.temp_dir.name)
        self.storage_base = root / "storage"
        self.storage_base.mkdir(parents=True)
        self.settings = FlyMailSettings(
            role="api",
            database_url=self.database_url(),
            data_dir=root,
            object_dir=root / "objects" / "sha256",
            object_tmp_dir=root / "objects" / ".tmp",
            session_secret="personal-api-session-secret-0123456789abcdef",
            db_pool_name="flymail-api",
            db_min_connections=2,
            db_max_connections=12,
        )
        self.admin = await self._create_user("personal-admin", "AdminPassword!123", role="admin")
        self.user = await self._create_user("personal-user", "UserPassword!123")
        self.other = await self._create_user("personal-other", "OtherPassword!123")
        await self._seed_mail_data()

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()
        await super().asyncTearDown()

    async def _clear_tables(self) -> None:
        tables = (
            "notification_deliveries", "notification_events", "notification_rules",
            "notification_image_publishers", "notification_channels", "worker_jobs",
            "content_references", "content_objects", "contacts", "authorized_storage_roots",
            "message_remote_instances", "messages", "mailboxes", "mail_identities",
            "provider_credentials", "mail_accounts", "audit_events", "realtime_events",
            "login_rate_limits", "user_sessions", "user_profiles", "user_settings", "users",
            "process_heartbeats",
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
                AdminContext("usr_personal_admin"),
                username=username,
                password_hash=hash_password(password),
                role=role,
            )
            await connection.commit()
        return user

    async def _seed_mail_data(self) -> None:
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
                        ("acc_personal", self.user.id, "owner@example.com", "owner@example.com"),
                        ("acc_personal_other", self.other.id, "other@example.com", "other@example.com"),
                    ),
                )
                await cursor.executemany(
                    """
                    INSERT INTO mail_identities (
                        id, user_uid, account_id, from_address,
                        normalized_from_address, display_name, reply_to,
                        signature_html, signature_text, is_default, is_verified,
                        created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, '', '', NULL, NULL, 1, 1, 1, 1)
                    """,
                    (
                        ("identity_personal", self.user.id, "acc_personal", "owner@example.com", "owner@example.com"),
                        ("identity_personal_other", self.other.id, "acc_personal_other", "other@example.com", "other@example.com"),
                    ),
                )
                await cursor.executemany(
                    """
                    INSERT INTO messages (
                        id, user_uid, canonical_message_key, message_id_header,
                        subject, normalized_subject, from_json, to_json,
                        received_at, created_at, updated_at
                    ) VALUES (%s, %s, %s, '', 'Hello', 'hello', %s, '[]', 1, 1, 1)
                    """,
                    (
                        ("msg_personal", self.user.id, "msg-personal", '[{"address":"sender@example.com","name":"Sender Name"}]'),
                        ("msg_personal_other", self.other.id, "msg-personal-other", '[{"address":"secret@example.com","name":"Other Sender"}]'),
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
    def csrf_headers(token: str, **extra: str) -> dict[str, str]:
        return {"Origin": ORIGIN, "X-CSRF-Token": token, **extra}

    async def test_profile_avatar_is_normalized_and_tenant_scoped(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.141") as client:
                csrf = await self.login(client, "personal-user", "UserPassword!123")
                updated = await client.patch(
                    "/api/v2/profile",
                    headers=self.csrf_headers(csrf),
                    json={"nickname": "Display User"},
                )
                avatar = await client.post(
                    "/api/v2/profile/avatar",
                    headers=self.csrf_headers(csrf, **{"Content-Type": "image/png"}),
                    content=png_bytes(),
                )
                rendered = await client.get("/api/v2/profile/avatar")
                malformed = await client.post(
                    "/api/v2/profile/avatar",
                    headers=self.csrf_headers(csrf, **{"Content-Type": "image/png"}),
                    content=b"not-an-image",
                )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["nickname"], "Display User")
        self.assertEqual(updated.json()["role"], "user")
        self.assertEqual(avatar.status_code, 200)
        self.assertNotIn("sha256", json.dumps(avatar.json()).casefold())
        self.assertEqual(rendered.status_code, 200)
        with Image.open(io.BytesIO(rendered.content)) as image:
            self.assertEqual(image.format, "WEBP")
            self.assertEqual(image.size, (256, 256))
        self.assertEqual(malformed.status_code, 422)
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM content_references WHERE user_uid=%s AND reference_kind='user_avatar'",
                (self.user.id,),
            ),
            1,
        )

    async def test_contacts_quick_add_autocomplete_signature_and_account_icon(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.142") as client:
                csrf = await self.login(client, "personal-user", "UserPassword!123")
                quick = await client.post(
                    "/api/v2/contacts/quick-add",
                    headers=self.csrf_headers(csrf),
                    json={"message_id": "msg_personal"},
                )
                autocomplete = await client.get("/api/v2/contacts/autocomplete?q=send")
                foreign = await client.post(
                    "/api/v2/contacts/quick-add",
                    headers=self.csrf_headers(csrf),
                    json={"message_id": "msg_personal_other"},
                )
                signature = await client.patch(
                    "/api/v2/accounts/acc_personal/identities/identity_personal",
                    headers=self.csrf_headers(csrf),
                    json={
                        "signature_html": '<script>alert(1)</script><b onclick="x()">Hello</b>',
                        "signature_text": "Hello",
                    },
                )
                preset = await client.put(
                    "/api/v2/accounts/acc_personal/icon",
                    headers=self.csrf_headers(csrf),
                    json={"mode": "preset", "value": "mail"},
                )
                uploaded = await client.post(
                    "/api/v2/accounts/acc_personal/icon/upload",
                    headers=self.csrf_headers(csrf, **{"Content-Type": "image/png"}),
                    content=png_bytes(180, 420),
                )
                icon_bytes = await client.get("/api/v2/accounts/acc_personal/icon/content")
                foreign_icon = await client.put(
                    "/api/v2/accounts/acc_personal_other/icon",
                    headers=self.csrf_headers(csrf),
                    json={"mode": "provider", "value": ""},
                )
        self.assertEqual(quick.status_code, 201)
        self.assertEqual(quick.json()["primary_email"], "sender@example.com")
        self.assertEqual([item["primary_email"] for item in autocomplete.json()["items"]], ["sender@example.com"])
        self.assertEqual(foreign.status_code, 404)
        self.assertEqual(signature.status_code, 200)
        self.assertNotIn("script", signature.json()["signature_html"].casefold())
        self.assertNotIn("onclick", signature.json()["signature_html"].casefold())
        self.assertIn("<b>Hello</b>", signature.json()["signature_html"])
        self.assertEqual(preset.json()["mode"], "preset")
        self.assertEqual(uploaded.json()["mode"], "uploaded")
        self.assertNotIn("sha256", json.dumps(uploaded.json()).casefold())
        self.assertEqual(icon_bytes.status_code, 200)
        with Image.open(io.BytesIO(icon_bytes.content)) as image:
            self.assertEqual(image.format, "WEBP")
            self.assertEqual(image.size, (256, 256))
        self.assertEqual(foreign_icon.status_code, 404)

    async def test_notification_channel_rule_secret_and_test_delivery_are_safe(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.143") as client:
                csrf = await self.login(client, "personal-user", "UserPassword!123")
                channel = await client.post(
                    "/api/v2/notification-channels",
                    headers=self.csrf_headers(csrf),
                    json={
                        "channel_key": "generic_webhook",
                        "display_name": "My webhook",
                        "enabled": True,
                        "public_config": {"endpoint_url": "https://example.com/hook"},
                        "secret": {"authorization": "Bearer very-secret-token"},
                        "use_proxy": True,
                    },
                )
                channel_id = channel.json()["id"]
                listing = await client.get("/api/v2/notification-channels")
                rule = await client.post(
                    "/api/v2/notification-rules",
                    headers=self.csrf_headers(csrf),
                    json={
                        "event_type": "mail.new",
                        "channel_id": channel_id,
                        "enabled": True,
                        "use_proxy": True,
                    },
                )
                tested = await client.post(
                    f"/api/v2/notification-channels/{channel_id}/test",
                    headers=self.csrf_headers(csrf),
                )
                private = await client.post(
                    "/api/v2/notification-channels",
                    headers=self.csrf_headers(csrf),
                    json={
                        "channel_key": "generic_webhook",
                        "display_name": "Private",
                        "public_config": {"endpoint_url": "http://127.0.0.1/hook"},
                        "secret": {},
                    },
                )
        self.assertEqual(channel.status_code, 201)
        self.assertTrue(channel.json()["secret_configured"])
        serialized = json.dumps([channel.json(), listing.json()]).casefold()
        for forbidden in ("very-secret-token", "ciphertext", "nonce", "authorization"):
            self.assertNotIn(forbidden, serialized)
        self.assertEqual(rule.status_code, 201)
        self.assertEqual(tested.status_code, 202)
        self.assertTrue(tested.json()["task_id"].startswith("job_"))
        self.assertEqual(private.status_code, 422)
        self.assertEqual(
            await self.scalar("SELECT COUNT(*) FROM worker_jobs WHERE job_kind='notification.deliver'"),
            1,
        )
        self.assertEqual(
            await self.scalar("SELECT COUNT(*) FROM notification_deliveries WHERE status='pending'"),
            1,
        )
        ciphertext = await self.scalar(
            "SELECT secret_ciphertext FROM notification_channels WHERE id=%s",
            (channel_id,),
        )
        self.assertIsNotNone(ciphertext)
        self.assertNotIn(b"very-secret-token", bytes(ciphertext))

    async def test_notification_configuration_crud_is_tenant_scoped(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.145") as client:
                csrf = await self.login(client, "personal-user", "UserPassword!123")
                channel = await client.post(
                    "/api/v2/notification-channels",
                    headers=self.csrf_headers(csrf),
                    json={
                        "channel_key": "generic_webhook",
                        "display_name": "First",
                        "public_config": {"endpoint_url": "https://example.com/first"},
                        "secret": {"token": "first-secret"},
                    },
                )
                channel_id = channel.json()["id"]
                publisher = await client.post(
                    "/api/v2/notification-publishers",
                    headers=self.csrf_headers(csrf),
                    json={
                        "publisher_key": "generic_https",
                        "display_name": "First publisher",
                        "endpoint_url": "https://example.com/upload-one",
                        "public_config": {},
                        "secret": {"token": "publisher-one"},
                    },
                )
                publisher_id = publisher.json()["id"]
                rule = await client.post(
                    "/api/v2/notification-rules",
                    headers=self.csrf_headers(csrf),
                    json={
                        "event_type": "mail.new",
                        "channel_id": channel_id,
                        "image_publisher_id": publisher_id,
                        "enabled": True,
                        "use_proxy": False,
                    },
                )
                rule_id = rule.json()["id"]
                updated_channel = await client.put(
                    f"/api/v2/notification-channels/{channel_id}",
                    headers=self.csrf_headers(csrf),
                    json={
                        "channel_key": "generic_webhook",
                        "display_name": "Updated",
                        "public_config": {"endpoint_url": "https://example.com/updated"},
                        "secret": {},
                        "enabled": False,
                    },
                )
                updated_publisher = await client.put(
                    f"/api/v2/notification-publishers/{publisher_id}",
                    headers=self.csrf_headers(csrf),
                    json={
                        "publisher_key": "generic_https",
                        "display_name": "Updated publisher",
                        "endpoint_url": "https://example.com/upload-two",
                        "public_config": {"url_field": "url"},
                        "secret": {},
                        "enabled": True,
                    },
                )
                updated_rule = await client.put(
                    f"/api/v2/notification-rules/{rule_id}",
                    headers=self.csrf_headers(csrf),
                    json={
                        "event_type": "send.failed",
                        "channel_id": channel_id,
                        "enabled": False,
                        "use_proxy": True,
                        "dedupe_window_seconds": 60,
                    },
                )
                channels = await client.get("/api/v2/notification-channels")
                rules = await client.get("/api/v2/notification-rules")
                publishers = await client.get("/api/v2/notification-publishers")
                await client.post("/api/v2/auth/logout", headers=self.csrf_headers(csrf))
                other_csrf = await self.login(client, "personal-other", "OtherPassword!123")
                foreign_update = await client.put(
                    f"/api/v2/notification-channels/{channel_id}",
                    headers=self.csrf_headers(other_csrf),
                    json={
                        "channel_key": "generic_webhook",
                        "display_name": "Foreign",
                        "public_config": {"endpoint_url": "https://example.com/foreign"},
                        "secret": {},
                    },
                )
                self.assertEqual((await client.get("/api/v2/notification-channels")).json()["items"], [])
                await client.post("/api/v2/auth/logout", headers=self.csrf_headers(other_csrf))
                csrf = await self.login(client, "personal-user", "UserPassword!123")
                deleted_rule = await client.delete(
                    f"/api/v2/notification-rules/{rule_id}",
                    headers=self.csrf_headers(csrf),
                )
                deleted_publisher = await client.delete(
                    f"/api/v2/notification-publishers/{publisher_id}",
                    headers=self.csrf_headers(csrf),
                )
                deleted_channel = await client.delete(
                    f"/api/v2/notification-channels/{channel_id}",
                    headers=self.csrf_headers(csrf),
                )
        self.assertEqual(updated_channel.status_code, 200)
        self.assertEqual(updated_channel.json()["display_name"], "Updated")
        self.assertTrue(updated_channel.json()["secret_configured"])
        self.assertEqual(updated_publisher.status_code, 200)
        self.assertTrue(updated_publisher.json()["secret_configured"])
        self.assertEqual(updated_rule.status_code, 200)
        self.assertTrue(updated_rule.json()["use_proxy"])
        self.assertEqual(len(channels.json()["items"]), 1)
        self.assertEqual(len(rules.json()), 1)
        self.assertEqual(len(publishers.json()), 1)
        self.assertEqual(foreign_update.status_code, 404)
        self.assertEqual(deleted_rule.status_code, 204)
        self.assertEqual(deleted_publisher.status_code, 204)
        self.assertEqual(deleted_channel.status_code, 204)

    async def test_image_publisher_and_storage_roots_reject_private_or_escape_paths(self):
        allowed = self.storage_base / "allowed"
        allowed.mkdir()
        (allowed / "visible.txt").write_text("visible", encoding="utf-8")
        outside = self.storage_base / "outside.txt"
        outside.write_text("outside", encoding="utf-8")
        (allowed / "escape").symlink_to(outside)

        async with self.running_app() as app:
            async with self.client(app, "203.0.113.144") as client:
                user_csrf = await self.login(client, "personal-user", "UserPassword!123")
                denied = await client.post(
                    "/api/v2/admin/storage-roots",
                    headers=self.csrf_headers(user_csrf),
                    json={"label": "Allowed", "path": str(allowed), "visibility_scope": "all"},
                )
                await client.post("/api/v2/auth/logout", headers=self.csrf_headers(user_csrf))
                admin_csrf = await self.login(client, "personal-admin", "AdminPassword!123")
                root = await client.post(
                    "/api/v2/admin/storage-roots",
                    headers=self.csrf_headers(admin_csrf),
                    json={"label": "Allowed", "path": str(allowed), "visibility_scope": "all"},
                )
                root_id = root.json()["id"]
                publisher = await client.post(
                    "/api/v2/notification-publishers",
                    headers=self.csrf_headers(admin_csrf),
                    json={
                        "publisher_key": "generic_https",
                        "display_name": "Images",
                        "endpoint_url": "https://example.com/upload",
                        "enabled": True,
                        "public_config": {"url_field": "url"},
                        "secret": {"upload_token": "publisher-secret"},
                    },
                )
                private_publisher = await client.post(
                    "/api/v2/notification-publishers",
                    headers=self.csrf_headers(admin_csrf),
                    json={
                        "publisher_key": "generic_https",
                        "display_name": "Private Images",
                        "endpoint_url": "https://127.0.0.1/upload",
                        "enabled": True,
                        "public_config": {},
                        "secret": {},
                    },
                )
                await client.post("/api/v2/auth/logout", headers=self.csrf_headers(admin_csrf))
                await self.login(client, "personal-user", "UserPassword!123")
                roots = await client.get("/api/v2/storage/roots")
                listing = await client.get(f"/api/v2/storage/roots/{root_id}/browse")
                traversal = await client.get(
                    f"/api/v2/storage/roots/{root_id}/browse?path=../outside.txt"
                )
                symlink = await client.get(
                    f"/api/v2/storage/roots/{root_id}/browse?path=escape"
                )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(root.status_code, 201)
        self.assertNotIn(str(allowed), json.dumps(root.json()))
        self.assertEqual(publisher.status_code, 201)
        self.assertTrue(publisher.json()["secret_configured"])
        self.assertNotIn("publisher-secret", json.dumps(publisher.json()))
        self.assertEqual(private_publisher.status_code, 422)
        self.assertEqual([item["id"] for item in roots.json()["items"]], [root_id])
        self.assertEqual([item["name"] for item in listing.json()["items"]], ["visible.txt"])
        self.assertEqual(traversal.status_code, 422)
        self.assertEqual(symlink.status_code, 422)


if __name__ == "__main__":
    import unittest

    unittest.main()
