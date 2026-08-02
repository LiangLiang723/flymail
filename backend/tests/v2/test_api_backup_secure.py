from __future__ import annotations

import base64
import hashlib
import json
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

import httpx

from flymail.config import FlyMailSettings
from flymail.infrastructure.db.migrations.runner import run_migrations
from flymail.infrastructure.security.credentials import CredentialCipher
from flymail.infrastructure.security.passwords import hash_password
from flymail.repositories.base import AdminContext
from flymail.repositories.users import UserRepository
from tests.v2.mysql_test_case import MySqlIsolatedAsyncioTestCase
from v2_dev import create_app


ORIGIN = "https://testserver"
BACKUP_PASSWORD = "Backup-Password!123456"
ACCOUNT_SECRET = "mailbox-authorization-secret"
NOTIFICATION_SECRET = "notification-delivery-secret"
MAGIC = b"FLYMAIL-BACKUP-V2\n"


def decode_b64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class SecureBackupApiTests(MySqlIsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await run_migrations(self.api_pool)
        await self._clear_tables()
        self.temp_dir = tempfile.TemporaryDirectory(prefix="flymail-v2-secure-backup-")
        root = Path(self.temp_dir.name)
        self.settings = FlyMailSettings(
            role="api",
            database_url=self.database_url(),
            data_dir=root,
            object_dir=root / "objects" / "sha256",
            object_tmp_dir=root / "objects" / ".tmp",
            session_secret="secure-backup-instance-secret-0123456789abcdef",
            db_pool_name="flymail-api",
            db_min_connections=2,
            db_max_connections=12,
        )
        self.settings.object_dir.mkdir(parents=True, exist_ok=True)
        self.admin = await self._create_user("secure-backup-admin", "AdminPassword!123", role="admin")
        self.user = await self._create_user("secure-backup-user", "UserPassword!123")
        await self._seed_business_and_transient_data()

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()
        await super().asyncTearDown()

    async def _clear_tables(self) -> None:
        tables = (
            "backup_archives", "backup_jobs", "notification_deliveries",
            "notification_events", "notification_rules", "notification_image_publishers",
            "notification_channels", "oauth_authorization_states", "provider_credentials",
            "outbound_proxy_configs", "draft_attachments", "draft_recipients", "draft_versions",
            "drafts", "mail_operations", "worker_jobs", "content_references",
            "content_objects", "body_search_documents", "message_bodies", "contacts",
            "authorized_storage_roots", "mail_identities", "mail_accounts", "audit_events",
            "realtime_events", "login_rate_limits", "user_sessions", "user_profiles",
            "user_settings", "users", "process_heartbeats",
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
                AdminContext("usr_secure_backup_admin"),
                username=username,
                password_hash=hash_password(password),
                role=role,
            )
            await connection.commit()
        return user

    async def _seed_business_and_transient_data(self) -> None:
        cipher = CredentialCipher.from_master_secret(self.settings.session_secret)
        account_encrypted = cipher.encrypt("acc_secure_backup", ACCOUNT_SECRET.encode())
        channel_encrypted = cipher.encrypt("channel_secure_backup", json.dumps({"token": NOTIFICATION_SECRET}).encode())
        avatar_bytes = b"secure-avatar-object"
        avatar_digest = hashlib.sha256(avatar_bytes).hexdigest()
        avatar_path = self.settings.object_dir / avatar_digest[:2] / avatar_digest
        avatar_path.parent.mkdir(parents=True, exist_ok=True)
        avatar_path.write_bytes(avatar_bytes)
        draft_bytes = b"secure-draft-attachment"
        draft_digest = hashlib.sha256(draft_bytes).hexdigest()
        draft_path = self.settings.object_dir / draft_digest[:2] / draft_digest
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        draft_path.write_bytes(draft_bytes)
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO mail_accounts (
                        id, user_uid, provider_key, email, normalized_email,
                        display_name, status, poll_interval_seconds, created_at, updated_at
                    ) VALUES ('acc_secure_backup', %s, 'gmail', 'backup@example.com',
                              'backup@example.com', 'Backup Account', 'active', 300, 1, 1)
                    """,
                    (self.user.id,),
                )
                await cursor.execute(
                    """
                    INSERT INTO mail_identities (
                        id, user_uid, account_id, from_address, normalized_from_address,
                        display_name, signature_html, signature_text,
                        is_default, is_verified, created_at, updated_at
                    ) VALUES ('identity_secure_backup', %s, 'acc_secure_backup',
                              'backup@example.com', 'backup@example.com', 'Backup Sender',
                              '<b>Signature</b>', 'Signature', 1, 1, 1, 1)
                    """,
                    (self.user.id,),
                )
                await cursor.execute(
                    """
                    INSERT INTO provider_credentials (
                        id, user_uid, account_id, credential_type, algorithm,
                        key_version, nonce, ciphertext, expires_at,
                        credential_version, created_at, updated_at
                    ) VALUES ('credential_secure_backup', %s, 'acc_secure_backup',
                              'password', %s, %s, %s, %s, NULL, 1, 1, 1)
                    """,
                    (
                        self.user.id,
                        account_encrypted.algorithm,
                        account_encrypted.key_version,
                        decode_b64(account_encrypted.nonce_b64),
                        decode_b64(account_encrypted.ciphertext_b64),
                    ),
                )
                await cursor.execute(
                    """
                    INSERT INTO contacts (
                        id, user_uid, display_name, normalized_name,
                        primary_email, normalized_email, emails_json,
                        created_at, updated_at
                    ) VALUES ('contact_secure_backup', %s, 'Backup Contact', 'backup contact',
                              'contact@example.com', 'contact@example.com',
                              '["contact@example.com"]', 1, 1)
                    """,
                    (self.user.id,),
                )
                storage_root = self.settings.data_dir / "authorized-root"
                storage_root.mkdir()
                await cursor.execute(
                    """
                    INSERT INTO authorized_storage_roots (
                        id, user_uid, label, root_path, visibility_scope,
                        enabled, created_by, created_at, updated_at
                    ) VALUES ('storage_secure_backup', %s, 'Authorized', %s,
                              'user', 1, %s, 1, 1)
                    """,
                    (self.user.id, str(storage_root), self.admin.id),
                )
                await cursor.execute(
                    """
                    INSERT INTO notification_channels (
                        id, user_uid, channel_key, display_name, enabled,
                        public_config, secret_algorithm, secret_key_version,
                        secret_nonce, secret_ciphertext, use_proxy,
                        created_at, updated_at
                    ) VALUES ('channel_secure_backup', %s, 'generic_webhook', 'Backup Hook', 1,
                              '{"endpoint_url":"https://example.com/hook"}', %s, %s, %s, %s,
                              0, 1, 1)
                    """,
                    (
                        self.user.id,
                        channel_encrypted.algorithm,
                        channel_encrypted.key_version,
                        decode_b64(channel_encrypted.nonce_b64),
                        decode_b64(channel_encrypted.ciphertext_b64),
                    ),
                )
                await cursor.execute(
                    """
                    INSERT INTO notification_rules (
                        id, user_uid, event_type, channel_id, enabled,
                        filter_json, dedupe_window_seconds, created_at, updated_at
                    ) VALUES ('rule_secure_backup', %s, 'mail.new',
                              'channel_secure_backup', 1, '{}', 0, 1, 1)
                    """,
                    (self.user.id,),
                )
                await cursor.execute(
                    """
                    INSERT INTO drafts (
                        id, user_uid, account_id, identity_id, subject,
                        version, status, send_state, send_message_id,
                        created_at, updated_at, queued_at
                    ) VALUES ('draft_secure_backup', %s, 'acc_secure_backup',
                              'identity_secure_backup', 'Pending draft', 1,
                              'queued', 'queued', '<stable@flymail>', 1, 1, 1)
                    """,
                    (self.user.id,),
                )
                await cursor.execute(
                    """
                    INSERT INTO draft_attachments (
                        id, draft_id, user_uid, content_sha256, filename,
                        content_type, size_bytes, position_index, created_at
                    ) VALUES ('draftatt_secure_backup', 'draft_secure_backup', %s, %s,
                              'draft.txt', 'text/plain', %s, 0, 1)
                    """,
                    (self.user.id, draft_digest, len(draft_bytes)),
                )
                await cursor.execute(
                    """
                    INSERT INTO mail_operations (
                        id, user_uid, operation_type, target_type, target_id,
                        account_id, desired_state, status, priority, available_at,
                        idempotency_key, created_at, updated_at
                    ) VALUES ('op_secure_backup', %s, 'move', 'message', 'msg_pending',
                              'acc_secure_backup', '{"mailbox_id":"archive"}', 'pending',
                              100, 0, 'secure-op', 1, 1)
                    """,
                    (self.user.id,),
                )
                for digest, kind, size, relative in (
                    (avatar_digest, "user_avatar", len(avatar_bytes), f"{avatar_digest[:2]}/{avatar_digest}"),
                    (draft_digest, "draft_attachment", len(draft_bytes), f"{draft_digest[:2]}/{draft_digest}"),
                ):
                    await cursor.execute(
                        """
                        INSERT INTO content_objects (
                            content_sha256, object_kind, compression,
                            original_size_bytes, stored_size_bytes, relative_path,
                            created_at
                        ) VALUES (%s, %s, 'none', %s, %s, %s, 1)
                        """,
                        (digest, kind, size, size, relative),
                    )
                await cursor.executemany(
                    """
                    INSERT INTO content_references (
                        id, user_uid, content_sha256, reference_kind,
                        reference_id, pinned, created_at, last_accessed_at
                    ) VALUES (%s, %s, %s, %s, %s, 1, 1, 1)
                    """,
                    (
                        ("ref_avatar_secure_backup", self.user.id, avatar_digest, "user_avatar", self.user.id),
                        ("ref_draft_secure_backup", self.user.id, draft_digest, "draft_attachment", "draftatt_secure_backup"),
                    ),
                )
                await cursor.execute(
                    "UPDATE user_profiles SET avatar_object_sha256=%s WHERE user_uid=%s",
                    (avatar_digest, self.user.id),
                )
                await cursor.execute(
                    """
                    INSERT INTO oauth_authorization_states (
                        id, user_uid, session_id, provider_key, state_hash,
                        pkce_algorithm, pkce_key_version, pkce_nonce,
                        pkce_ciphertext, redirect_uri, expires_at, created_at
                    ) VALUES ('oauth_transient', %s, 'session_transient', 'gmail', %s,
                              'S256', 1, X'01', X'02', 'https://example.com/callback', 999, 1)
                    """,
                    (self.user.id, "f" * 64),
                )
                await cursor.execute(
                    """
                    INSERT INTO body_search_documents (
                        message_id, user_uid, subject_text, participants_text,
                        body_text, updated_at
                    ) VALUES ('body_transient', %s, 'subject', 'sender', 'cached body', 1)
                    """,
                    (self.user.id,),
                )
                await cursor.execute(
                    """
                    INSERT INTO notification_events (
                        id, user_uid, event_type, title, dedupe_key, created_at
                    ) VALUES ('event_transient', %s, 'mail.new', 'Transient', 'transient', 1)
                    """,
                    (self.user.id,),
                )
                await cursor.execute(
                    """
                    INSERT INTO notification_deliveries (
                        id, user_uid, notification_event_id, channel_id,
                        status, available_at, idempotency_key, created_at, updated_at
                    ) VALUES ('delivery_transient', %s, 'event_transient',
                              'channel_secure_backup', 'pending', 1, 'delivery-transient', 1, 1)
                    """,
                    (self.user.id,),
                )
            await connection.commit()

    @asynccontextmanager
    async def running_app(self, settings: FlyMailSettings | None = None):
        app = create_app(settings or self.settings)
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
            json={"username": "secure-backup-admin", "password": "AdminPassword!123"},
        )
        self.assertEqual(response.status_code, 200)
        return str(response.json()["csrf_token"])

    @staticmethod
    def csrf_headers(token: str) -> dict[str, str]:
        return {"Origin": ORIGIN, "X-CSRF-Token": token}

    async def create_backup(self, client: httpx.AsyncClient, csrf: str) -> dict:
        response = await client.post(
            "/api/v2/admin/backups",
            headers=self.csrf_headers(csrf),
            json={"password": BACKUP_PASSWORD},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    async def test_archive_is_password_encrypted_and_scope_is_explicit(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.150") as client:
                csrf = await self.login(client)
                created = await self.create_backup(client, csrf)
                inspection = await client.post(
                    f"/api/v2/admin/backups/{created['id']}/inspect",
                    headers=self.csrf_headers(csrf),
                    json={"password": BACKUP_PASSWORD},
                )
                download = await client.get(
                    f"/api/v2/admin/backups/{created['id']}/download"
                )
        self.assertEqual(created["status"], "completed")
        self.assertTrue(created["encrypted"])
        self.assertTrue(download.content.startswith(MAGIC))
        for secret in (BACKUP_PASSWORD, ACCOUNT_SECRET, NOTIFICATION_SECRET):
            self.assertNotIn(secret.encode(), download.content)
        body = inspection.json()
        self.assertTrue(body["valid"])
        self.assertTrue(body["encrypted"])
        self.assertGreaterEqual(body["encrypted_secret_count"], 2)
        self.assertGreaterEqual(body["business_object_count"], 2)
        for required in (
            "users", "user_profiles", "contacts", "mail_accounts", "mail_identities",
            "provider_credentials", "notification_channels", "notification_rules",
            "drafts", "draft_attachments", "mail_operations", "authorized_storage_roots",
        ):
            self.assertIn(required, body["included_tables"])
        for excluded in (
            "oauth_authorization_states", "notification_deliveries",
            "body_search_documents", "worker_jobs", "realtime_events",
        ):
            self.assertIn(excluded, body["excluded_tables"])
        stored_manifest = await self.scalar(
            "SELECT manifest_json FROM backup_archives WHERE id=%s",
            (created["id"],),
        )
        self.assertNotIn(BACKUP_PASSWORD, str(stored_manifest))
        self.assertEqual(
            await self.scalar("SELECT COUNT(*) FROM worker_jobs WHERE job_kind LIKE 'backup.%%'"),
            0,
        )

    async def test_wrong_password_and_corrupt_ciphertext_fail_before_database_changes(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.151") as client:
                csrf = await self.login(client)
                created = await self.create_backup(client, csrf)
                wrong = await client.post(
                    f"/api/v2/admin/backups/{created['id']}/inspect",
                    headers=self.csrf_headers(csrf),
                    json={"password": "wrong-backup-password"},
                )
                archive_name = created["archive_name"]
                archive_path = self.settings.data_dir / "backups" / archive_name
                raw = bytearray(archive_path.read_bytes())
                raw[-20] ^= 0x01
                archive_path.write_bytes(raw)
                corrupted_sha = hashlib.sha256(raw).hexdigest()
                async with self.api_pool.acquire() as connection:
                    async with connection.cursor() as cursor:
                        await cursor.execute(
                            "UPDATE backup_archives SET archive_sha256=%s WHERE id=%s",
                            (corrupted_sha, created["id"]),
                        )
                    await connection.commit()
                corrupt = await client.post(
                    f"/api/v2/admin/backups/{created['id']}/inspect",
                    headers=self.csrf_headers(csrf),
                    json={"password": BACKUP_PASSWORD},
                )
        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(wrong.json()["error"]["code"], "backup_password_invalid")
        self.assertEqual(corrupt.status_code, 409)
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name LIKE 'flymail_restore_%%' OR schema_name LIKE 'flymail_snapshot_%%'"
            ),
            0,
        )

    async def test_restore_rehearsal_reencrypts_secrets_and_marks_unfinished_work_for_review(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.152") as client:
                csrf = await self.login(client)
                created = await self.create_backup(client, csrf)
        rotated = FlyMailSettings(
            role="api",
            database_url=self.settings.database_url,
            data_dir=self.settings.data_dir,
            object_dir=self.settings.object_dir,
            object_tmp_dir=self.settings.object_tmp_dir,
            session_secret="rotated-instance-secret-abcdefghijklmnopqrstuvwxyz",
            db_pool_name="flymail-api-rotated",
            db_min_connections=2,
            db_max_connections=12,
        )
        async with self.running_app(rotated) as app:
            async with self.client(app, "203.0.113.153") as client:
                csrf = await self.login(client)
                response = await client.post(
                    f"/api/v2/admin/backups/{created['id']}/restore-rehearsal",
                    headers=self.csrf_headers(csrf),
                    json={"password": BACKUP_PASSWORD},
                )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertGreaterEqual(payload["re_encrypted_secret_count"], 2)
        self.assertGreaterEqual(payload["review_required_operation_count"], 1)
        self.assertGreaterEqual(payload["review_required_draft_count"], 1)
        self.assertTrue(payload["temporary_database_removed"])
        self.assertTrue(payload["temporary_files_removed"])
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name LIKE 'flymail_restore_%%' OR schema_name LIKE 'flymail_snapshot_%%'"
            ),
            0,
        )


if __name__ == "__main__":
    import unittest

    unittest.main()
