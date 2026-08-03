from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import os
import struct
import tarfile
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from flymail.config import FlyMailSettings
from flymail.infrastructure.db.migrations.runner import LATEST_SCHEMA_VERSION, run_migrations
from flymail.infrastructure.security.passwords import hash_password
from flymail.repositories.base import AdminContext
from flymail.repositories.users import UserRepository
from tests.v2.mysql_test_case import MySqlIsolatedAsyncioTestCase
from flymail.api.app import create_app


ORIGIN = "https://testserver"
BACKUP_PASSWORD = "Backup-Archive!123456"
MAGIC = b"FLYMAIL-BACKUP-V2\n"


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def encrypt_backup_sample(source: Path, destination: Path, password: str) -> None:
    salt = os.urandom(16)
    nonce = os.urandom(12)
    key = Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(password.encode())
    verifier = hmac.new(key, b"flymail-backup-password-check-v2", hashlib.sha256).digest()
    header = {
        "format_version": 2,
        "cipher": "AES-256-GCM",
        "kdf": "scrypt",
        "scrypt_n": 2**15,
        "scrypt_r": 8,
        "scrypt_p": 1,
        "salt": _encode(salt),
        "nonce": _encode(nonce),
        "password_verifier": _encode(verifier),
    }
    header_bytes = json.dumps(header, sort_keys=True, separators=(",", ":")).encode()
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(header_bytes)
    with source.open("rb") as input_handle, destination.open("wb") as output_handle:
        output_handle.write(MAGIC)
        output_handle.write(struct.pack(">I", len(header_bytes)))
        output_handle.write(header_bytes)
        while chunk := input_handle.read(1024 * 1024):
            output_handle.write(encryptor.update(chunk))
        output_handle.write(encryptor.finalize())
        output_handle.write(encryptor.tag)


class BackupApiTests(MySqlIsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await run_migrations(self.api_pool)
        await self._clear_tables()
        self.temp_dir = tempfile.TemporaryDirectory(prefix="flymail-v2-backup-api-")
        root = Path(self.temp_dir.name)
        self.settings = FlyMailSettings(
            role="api",
            database_url=self.database_url(),
            data_dir=root,
            object_dir=root / "objects" / "sha256",
            object_tmp_dir=root / "objects" / ".tmp",
            session_secret="backup-api-session-secret-0123456789abcdef",
            db_pool_name="flymail-api",
            db_min_connections=2,
            db_max_connections=12,
        )
        self.settings.object_dir.mkdir(parents=True, exist_ok=True)
        self.admin = await self._create_user("backup-admin", "AdminPassword!123", role="admin")
        self.user = await self._create_user("backup-user", "UserPassword!123")

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()
        await super().asyncTearDown()

    async def _clear_tables(self) -> None:
        tables = (
            "backup_archives", "backup_jobs", "notification_deliveries",
            "notification_events", "notification_rules", "notification_image_publishers",
            "notification_channels", "oauth_authorization_states", "provider_credentials",
            "outbound_proxy_configs", "draft_attachments", "draft_recipients", "draft_versions",
            "drafts", "send_attempts", "mail_operations", "bulk_mail_operations",
            "worker_jobs", "job_attempts", "outbox_events", "realtime_events",
            "content_references", "content_objects", "body_search_documents",
            "message_bodies", "message_body_parts", "message_attachments",
            "message_memberships", "message_remote_instances", "message_headers",
            "thread_projections", "thread_messages", "threads", "messages",
            "sync_cursors", "account_runtime_state", "mailboxes", "saved_searches",
            "search_history", "contacts", "authorized_storage_roots", "mail_identities",
            "mail_accounts", "audit_events", "login_rate_limits", "user_sessions",
            "user_profiles", "user_settings", "users", "process_heartbeats",
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
                AdminContext("usr_backup_test_admin"),
                username=username,
                password_hash=hash_password(password),
                role=role,
            )
            await connection.commit()
        return user

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

    async def create_backup(self, client: httpx.AsyncClient, csrf: str) -> dict:
        response = await client.post(
            "/api/v2/admin/backups",
            headers=self.csrf_headers(csrf),
            json={"password": BACKUP_PASSWORD},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    async def test_admin_backup_create_list_download_and_inspect(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.140") as client:
                csrf = await self.login(client, "backup-admin", "AdminPassword!123")
                created = await self.create_backup(client, csrf)
                listing = await client.get("/api/v2/admin/backups")
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
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()["items"][0]["id"], created["id"])
        self.assertEqual(inspection.status_code, 200)
        self.assertTrue(inspection.json()["valid"])
        self.assertTrue(inspection.json()["compatible"])
        self.assertTrue(inspection.json()["encrypted"])
        self.assertGreaterEqual(inspection.json()["file_count"], 1)
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.headers["x-content-type-options"], "nosniff")
        self.assertTrue(download.content.startswith(MAGIC))
        self.assertNotIn(BACKUP_PASSWORD.encode(), download.content)
        rendered = json.dumps(created, ensure_ascii=False).casefold()
        for forbidden in ("password", "database_url", "session_secret", "root_path"):
            self.assertNotIn(forbidden, rendered)

    async def test_normal_user_cannot_create_or_list_backups(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.141") as client:
                csrf = await self.login(client, "backup-user", "UserPassword!123")
                created = await client.post(
                    "/api/v2/admin/backups",
                    headers=self.csrf_headers(csrf),
                    json={"password": BACKUP_PASSWORD},
                )
                listing = await client.get("/api/v2/admin/backups")
        self.assertEqual(created.status_code, 403)
        self.assertEqual(listing.status_code, 403)
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM backup_archives"), 0)

    async def test_inspection_rejects_path_traversal_archive(self):
        backups = self.settings.data_dir / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        archive_name = "unsafe.flymailbak"
        archive_path = backups / archive_name
        plaintext_path = backups / "unsafe-plain.tar.gz"
        manifest = {
            "format_version": 2,
            "encrypted": True,
            "app_version": "0.0.25",
            "schema_version": 16,
            "included_tables": [],
            "excluded_tables": [],
            "encrypted_secret_count": 0,
            "business_object_count": 0,
            "files": [],
        }
        with tarfile.open(plaintext_path, "w:gz") as archive:
            manifest_bytes = json.dumps(manifest).encode()
            info = tarfile.TarInfo("manifest.json")
            info.size = len(manifest_bytes)
            archive.addfile(info, io.BytesIO(manifest_bytes))
            unsafe = tarfile.TarInfo("../escape")
            unsafe.size = 1
            archive.addfile(unsafe, io.BytesIO(b"x"))
        encrypt_backup_sample(plaintext_path, archive_path, BACKUP_PASSWORD)
        plaintext_path.unlink()
        sha = hashlib.sha256(archive_path.read_bytes()).hexdigest()
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO backup_archives (
                        id, created_by, status, archive_name, archive_sha256,
                        size_bytes, manifest_json, created_at, updated_at, completed_at
                    ) VALUES ('backup_unsafe', %s, 'completed', %s, %s, %s, %s, 1, 1, 1)
                    """,
                    (
                        self.admin.id,
                        archive_name,
                        sha,
                        archive_path.stat().st_size,
                        json.dumps(manifest),
                    ),
                )
            await connection.commit()
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.142") as client:
                csrf = await self.login(client, "backup-admin", "AdminPassword!123")
                response = await client.post(
                    "/api/v2/admin/backups/backup_unsafe/inspect",
                    headers=self.csrf_headers(csrf),
                    json={"password": BACKUP_PASSWORD},
                )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "unsafe_backup_archive")
        self.assertFalse((self.settings.data_dir / "escape").exists())

    async def test_restore_rehearsal_uses_and_removes_temporary_database(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.143") as client:
                csrf = await self.login(client, "backup-admin", "AdminPassword!123")
                created = await self.create_backup(client, csrf)
                response = await client.post(
                    f"/api/v2/admin/backups/{created['id']}/restore-rehearsal",
                    headers=self.csrf_headers(csrf),
                    json={"password": BACKUP_PASSWORD},
                )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertGreater(payload["restored_table_count"], 0)
        self.assertEqual(payload["restored_schema_version"], LATEST_SCHEMA_VERSION)
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
