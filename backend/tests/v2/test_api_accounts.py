from __future__ import annotations

import base64
import hashlib
import json
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from flymail.config import FlyMailSettings
from flymail.infrastructure.db.migrations.runner import run_migrations
from flymail.infrastructure.security.credentials import CredentialCipher
from flymail.infrastructure.security.passwords import hash_password
from flymail.repositories.accounts import CredentialRepository
from flymail.repositories.base import AdminContext, TenantContext
from flymail.repositories.jobs import JobRepository, JobSpec
from flymail.repositories.users import UserRepository
from tests.v2.mysql_test_case import MySqlIsolatedAsyncioTestCase
from flymail.api.app import create_app


ORIGIN = "https://testserver"


class FakeOAuthGateway:
    def __init__(self) -> None:
        self.authorization_calls: list[dict] = []
        self.exchange_calls: list[dict] = []

    def build_authorization_url(
        self,
        *,
        provider_key: str,
        state: str,
        code_challenge: str,
        redirect_uri: str,
        proxy_url: str | None,
    ) -> str:
        self.authorization_calls.append(
            {
                "provider_key": provider_key,
                "state": state,
                "code_challenge": code_challenge,
                "redirect_uri": redirect_uri,
                "proxy_url": proxy_url,
            }
        )
        return f"https://oauth.example/authorize?state={state}&challenge={code_challenge}"

    async def exchange_code(
        self,
        *,
        provider_key: str,
        code: str,
        code_verifier: str,
        redirect_uri: str,
        proxy_url: str | None,
    ) -> dict:
        self.exchange_calls.append(
            {
                "provider_key": provider_key,
                "code": code,
                "code_verifier": code_verifier,
                "redirect_uri": redirect_uri,
                "proxy_url": proxy_url,
            }
        )
        return {
            "access_token": "oauth-access-token-never-return",
            "refresh_token": "oauth-refresh-token-never-return",
            "expires_at": 2_000_000_000.0,
        }


class AccountApiTests(MySqlIsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await run_migrations(self.api_pool)
        await self._clear_tables()
        self.temp_dir = tempfile.TemporaryDirectory(prefix="flymail-v2-account-api-")
        root = Path(self.temp_dir.name)
        self.settings = FlyMailSettings(
            role="api",
            database_url=self.database_url(),
            data_dir=root,
            object_dir=root / "objects" / "sha256",
            object_tmp_dir=root / "objects" / ".tmp",
            session_secret="account-api-session-secret-0123456789abcdef",
            db_pool_name="flymail-api",
            db_min_connections=2,
            db_max_connections=12,
        )
        self.user = await self._create_user("account-user", "AccountPassword!123")
        self.other = await self._create_user("other-account-user", "OtherPassword!123")
        self.cipher = CredentialCipher.from_master_secret(self.settings.session_secret)

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()
        await super().asyncTearDown()

    async def _clear_tables(self) -> None:
        tables = (
            "audit_events",
            "outbox_events",
            "job_attempts",
            "worker_jobs",
            "account_runtime_state",
            "outbound_proxy_configs",
            "oauth_authorization_states",
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
                AdminContext("usr_account_test_admin"),
                username=username,
                password_hash=hash_password(password),
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

    @staticmethod
    def csrf_headers(csrf_token: str) -> dict[str, str]:
        return {"Origin": ORIGIN, "X-CSRF-Token": csrf_token}

    async def rows(self, sql: str, params: tuple | list = ()) -> list[tuple]:
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(sql, params)
                return list(await cursor.fetchall())

    async def test_create_encrypts_credential_and_response_is_secret_free(self):
        secret = "qq-authorization-code-never-return"
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.30") as client:
                csrf = await self.login(client, "account-user", "AccountPassword!123")
                response = await client.post(
                    "/api/v2/accounts",
                    headers=self.csrf_headers(csrf),
                    json={
                        "provider_key": "qq",
                        "email": "Primary@Example.com",
                        "display_name": "Primary mailbox",
                        "credential_type": "authorization_code",
                        "credential": secret,
                    },
                )

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["provider_key"], "qq")
        self.assertEqual(payload["email"], "Primary@Example.com")
        self.assertEqual(payload["status"], "pending")
        rendered_response = json.dumps(payload, ensure_ascii=False)
        for forbidden in (
            secret,
            "credential",
            "ciphertext",
            "nonce",
            "password",
            "authorization_code",
        ):
            self.assertNotIn(forbidden, rendered_response)

        account_id = str(payload["id"])
        async with self.api_pool.acquire() as connection:
            encrypted = await CredentialRepository(connection).get_encrypted(
                TenantContext(self.user.id),
                account_id,
            )
        self.assertIsNotNone(encrypted)
        assert encrypted is not None
        self.assertEqual(encrypted.credential_type, "authorization_code")
        self.assertEqual(
            self.cipher.decrypt(account_id, encrypted.value),
            secret.encode("utf-8"),
        )
        self.assertNotIn(secret, repr(encrypted))

        identities = await self.rows(
            """
            SELECT from_address, is_default, is_verified
            FROM mail_identities
            WHERE account_id = %s AND user_uid = %s
            """,
            (account_id, self.user.id),
        )
        self.assertEqual(identities, [("Primary@Example.com", 1, 1)])
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM account_runtime_state WHERE account_id=%s AND user_uid=%s",
                (account_id, self.user.id),
            ),
            1,
        )
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM outbox_events WHERE aggregate_id=%s AND event_type='account.created'",
                (account_id,),
            ),
            1,
        )
        database_rendered = json.dumps(
            await self.rows(
                """
                SELECT payload FROM outbox_events WHERE aggregate_id=%s
                UNION ALL
                SELECT safe_metadata FROM audit_events WHERE resource_id=%s
                """,
                (account_id, account_id),
            ),
            ensure_ascii=False,
            default=str,
        )
        self.assertNotIn(secret, database_rendered)

    async def test_invalid_email_and_proxy_credentials_are_validation_errors(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.43") as client:
                csrf = await self.login(client, "account-user", "AccountPassword!123")
                invalid_email = await client.post(
                    "/api/v2/accounts",
                    headers=self.csrf_headers(csrf),
                    json={
                        "provider_key": "qq",
                        "email": "not-an-email",
                        "credential_type": "authorization_code",
                        "credential": "invalid-email-secret",
                    },
                )
                self.assertEqual(invalid_email.status_code, 422)
                self.assertEqual(
                    invalid_email.json()["error"]["code"],
                    "validation_error",
                )

                invalid_proxy = await client.put(
                    "/api/v2/accounts/proxy",
                    headers=self.csrf_headers(csrf),
                    json={
                        "scheme": "http",
                        "host": "8.8.8.8",
                        "port": 8080,
                        "password": "password-without-username",
                    },
                )
                self.assertEqual(invalid_proxy.status_code, 422)
                self.assertEqual(
                    invalid_proxy.json()["error"]["code"],
                    "validation_error",
                )
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM mail_accounts"), 0)
        self.assertEqual(
            await self.scalar("SELECT COUNT(*) FROM outbound_proxy_configs"),
            0,
        )

    async def test_unknown_provider_is_rejected_without_database_side_effects(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.41") as client:
                csrf = await self.login(client, "account-user", "AccountPassword!123")
                response = await client.post(
                    "/api/v2/accounts",
                    headers=self.csrf_headers(csrf),
                    json={
                        "provider_key": "unknown-provider",
                        "email": "unknown@example.com",
                        "credential_type": "password",
                        "credential": "unknown-provider-secret",
                    },
                )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "unsupported_provider")
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM mail_accounts"), 0)
        self.assertNotIn("unknown-provider-secret", response.text)

    async def test_duplicate_scope_listing_update_and_cross_tenant_access(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.31") as first_client:
                first_csrf = await self.login(
                    first_client,
                    "account-user",
                    "AccountPassword!123",
                )
                created = await first_client.post(
                    "/api/v2/accounts",
                    headers=self.csrf_headers(first_csrf),
                    json={
                        "provider_key": "qq",
                        "email": "Shared@Example.com",
                        "credential_type": "authorization_code",
                        "credential": "first-user-code",
                    },
                )
                self.assertEqual(created.status_code, 201)
                account_id = str(created.json()["id"])
                duplicate = await first_client.post(
                    "/api/v2/accounts",
                    headers=self.csrf_headers(first_csrf),
                    json={
                        "provider_key": "qq",
                        "email": " shared@example.COM ",
                        "credential_type": "authorization_code",
                        "credential": "duplicate-code",
                    },
                )
                self.assertEqual(duplicate.status_code, 409)

                listing = await first_client.get("/api/v2/accounts")
                self.assertEqual(listing.status_code, 200)
                self.assertEqual([item["id"] for item in listing.json()["items"]], [account_id])

                updated = await first_client.patch(
                    f"/api/v2/accounts/{account_id}",
                    headers=self.csrf_headers(first_csrf),
                    json={
                        "display_name": "Updated mailbox",
                        "remark": "Private remark",
                        "group_name": "Work",
                        "poll_interval_seconds": 600,
                    },
                )
                self.assertEqual(updated.status_code, 200)
                self.assertEqual(updated.json()["display_name"], "Updated mailbox")
                self.assertEqual(updated.json()["remark"], "Private remark")
                self.assertEqual(updated.json()["group_name"], "Work")
                self.assertEqual(updated.json()["poll_interval_seconds"], 600)

            async with self.client(app, "203.0.113.32") as other_client:
                other_csrf = await self.login(
                    other_client,
                    "other-account-user",
                    "OtherPassword!123",
                )
                same_email = await other_client.post(
                    "/api/v2/accounts",
                    headers=self.csrf_headers(other_csrf),
                    json={
                        "provider_key": "qq",
                        "email": "shared@example.com",
                        "credential_type": "authorization_code",
                        "credential": "other-user-code",
                    },
                )
                self.assertEqual(same_email.status_code, 201)
                self.assertNotEqual(same_email.json()["id"], account_id)

                hidden = await other_client.get(f"/api/v2/accounts/{account_id}")
                self.assertEqual(hidden.status_code, 404)
                hidden_update = await other_client.patch(
                    f"/api/v2/accounts/{account_id}",
                    headers=self.csrf_headers(other_csrf),
                    json={"display_name": "Cross tenant"},
                )
                self.assertEqual(hidden_update.status_code, 404)

    async def test_partial_update_preserves_fields_and_enable_disable_controls_jobs(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.42") as client:
                csrf = await self.login(client, "account-user", "AccountPassword!123")
                created = await client.post(
                    "/api/v2/accounts",
                    headers=self.csrf_headers(csrf),
                    json={
                        "provider_key": "qq",
                        "email": "toggle@example.com",
                        "display_name": "Initial",
                        "credential_type": "authorization_code",
                        "credential": "toggle-auth-code",
                    },
                )
                self.assertEqual(created.status_code, 201)
                account_id = str(created.json()["id"])

                configured = await client.patch(
                    f"/api/v2/accounts/{account_id}",
                    headers=self.csrf_headers(csrf),
                    json={
                        "display_name": "Configured",
                        "remark": "Keep remark",
                        "group_name": "Keep group",
                        "poll_interval_seconds": 900,
                    },
                )
                self.assertEqual(configured.status_code, 200)

                async with self.api_pool.acquire() as connection:
                    await connection.begin()
                    sync_job = await JobRepository(connection).enqueue(
                        JobSpec(
                            queue_name="background",
                            job_kind="sync.reconcile",
                            user_uid=self.user.id,
                            account_id=account_id,
                            provider_key="qq",
                            payload={"account_id": account_id},
                            dedupe_key="toggle-sync-job",
                        )
                    )
                    await connection.commit()

                partial = await client.patch(
                    f"/api/v2/accounts/{account_id}",
                    headers=self.csrf_headers(csrf),
                    json={"display_name": "Partial rename"},
                )
                self.assertEqual(partial.status_code, 200)
                self.assertEqual(partial.json()["display_name"], "Partial rename")
                self.assertEqual(partial.json()["remark"], "Keep remark")
                self.assertEqual(partial.json()["group_name"], "Keep group")
                self.assertEqual(partial.json()["poll_interval_seconds"], 900)

                unchanged_updated_at = float(
                    await self.scalar(
                        "SELECT updated_at FROM mail_accounts WHERE id=%s",
                        (account_id,),
                    )
                )
                with patch(
                    "flymail.repositories.accounts.time",
                    SimpleNamespace(time=lambda: unchanged_updated_at),
                ):
                    disabled = await client.patch(
                        f"/api/v2/accounts/{account_id}",
                        headers=self.csrf_headers(csrf),
                        json={"enabled": False},
                    )
                    disabled_again = await client.patch(
                        f"/api/v2/accounts/{account_id}",
                        headers=self.csrf_headers(csrf),
                        json={"enabled": False},
                    )
                self.assertEqual(disabled.status_code, 200)
                self.assertEqual(disabled_again.status_code, 200)
                self.assertEqual(disabled.json()["status"], "disabled")
                self.assertEqual(
                    await self.scalar("SELECT status FROM worker_jobs WHERE id=%s", (sync_job,)),
                    "cancelled",
                )
                self.assertEqual(
                    await self.scalar(
                        "SELECT status FROM account_runtime_state WHERE account_id=%s",
                        (account_id,),
                    ),
                    "disabled",
                )

                enabled = await client.patch(
                    f"/api/v2/accounts/{account_id}",
                    headers=self.csrf_headers(csrf),
                    json={"enabled": True},
                )
                self.assertEqual(enabled.status_code, 200)
                self.assertEqual(enabled.json()["status"], "pending")

        verify_jobs = await self.rows(
            """
            SELECT job_kind, status, payload
            FROM worker_jobs
            WHERE account_id=%s AND job_kind='account.verify'
            ORDER BY created_at, id
            """,
            (account_id,),
        )
        self.assertEqual(len(verify_jobs), 1)
        self.assertEqual(verify_jobs[0][0:2], ("account.verify", "pending"))
        payload = json.loads(verify_jobs[0][2]) if isinstance(verify_jobs[0][2], str) else verify_jobs[0][2]
        self.assertEqual(set(payload), {"account_id", "credential_version"})

    async def test_custom_endpoints_are_public_and_verify_only_enqueues_safe_job(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.33") as client:
                csrf = await self.login(client, "account-user", "AccountPassword!123")
                rejected = await client.post(
                    "/api/v2/accounts",
                    headers=self.csrf_headers(csrf),
                    json={
                        "provider_key": "generic",
                        "email": "private-endpoint@example.com",
                        "credential_type": "password",
                        "credential": "mail-password-never-log",
                        "endpoint_config": {
                            "imap": {"host": "127.0.0.1", "port": 993, "security": "tls"},
                            "smtp": {"host": "169.254.169.254", "port": 465, "security": "tls"},
                        },
                    },
                )
                self.assertEqual(rejected.status_code, 422)
                self.assertEqual(rejected.json()["error"]["code"], "unsafe_endpoint")
                self.assertEqual(await self.scalar("SELECT COUNT(*) FROM mail_accounts"), 0)

                created = await client.post(
                    "/api/v2/accounts",
                    headers=self.csrf_headers(csrf),
                    json={
                        "provider_key": "generic",
                        "email": "public-endpoint@example.com",
                        "credential_type": "password",
                        "credential": "mail-password-never-log",
                        "endpoint_config": {
                            "imap": {"host": "8.8.8.8", "port": 993, "security": "tls"},
                            "smtp": {"host": "1.1.1.1", "port": 587, "security": "starttls"},
                        },
                    },
                )
                self.assertEqual(created.status_code, 201)
                account_id = str(created.json()["id"])
                self.assertEqual(created.json()["endpoint_config"]["imap"]["host"], "8.8.8.8")

                verified = await client.post(
                    f"/api/v2/accounts/{account_id}/verify",
                    headers=self.csrf_headers(csrf),
                )
                self.assertEqual(verified.status_code, 202)
                self.assertTrue(verified.json()["job_id"])
                self.assertEqual(
                    verified.json()["status_url"],
                    f"/api/v2/jobs/{verified.json()['job_id']}",
                )

        jobs = await self.rows(
            """
            SELECT user_uid, account_id, provider_key, queue_name, job_kind,
                   priority, status, payload
            FROM worker_jobs
            """
        )
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0][0:7], (
            self.user.id,
            account_id,
            "generic",
            "interactive",
            "account.verify",
            0,
            "pending",
        ))
        payload = json.loads(jobs[0][7]) if isinstance(jobs[0][7], str) else jobs[0][7]
        self.assertEqual(set(payload), {"account_id", "credential_version"})
        self.assertEqual(payload["account_id"], account_id)
        rendered = json.dumps(jobs, ensure_ascii=False, default=str)
        self.assertNotIn("mail-password-never-log", rendered)
        self.assertNotIn("ciphertext", rendered)

    async def test_user_oauth_proxy_encrypts_all_credentials_and_is_secret_free(self):
        proxy_username = "proxy-user-never-return"
        proxy_password = "proxy-password-never-return"
        async with self.running_app() as app:
            original_database_url = app.state.settings.database_url
            async with self.client(app, "203.0.113.34") as client:
                csrf = await self.login(client, "account-user", "AccountPassword!123")
                rejected = await client.put(
                    "/api/v2/accounts/proxy",
                    headers=self.csrf_headers(csrf),
                    json={
                        "scheme": "http",
                        "host": "10.0.0.1",
                        "port": 8080,
                        "username": proxy_username,
                        "password": proxy_password,
                    },
                )
                self.assertEqual(rejected.status_code, 422)
                self.assertEqual(rejected.json()["error"]["code"], "unsafe_endpoint")

                saved = await client.put(
                    "/api/v2/accounts/proxy",
                    headers=self.csrf_headers(csrf),
                    json={
                        "scheme": "http",
                        "host": "8.8.4.4",
                        "port": 8080,
                        "username": proxy_username,
                        "password": proxy_password,
                    },
                )
                self.assertEqual(saved.status_code, 200)
                self.assertEqual(saved.json()["host"], "8.8.4.4")
                self.assertTrue(saved.json()["has_credentials"])
                rendered_response = json.dumps(saved.json(), ensure_ascii=False)
                self.assertNotIn(proxy_username, rendered_response)
                self.assertNotIn(proxy_password, rendered_response)
                self.assertNotIn("ciphertext", rendered_response)

                loaded = await client.get("/api/v2/accounts/proxy")
                self.assertEqual(loaded.status_code, 200)
                self.assertEqual(loaded.json(), saved.json())
            self.assertEqual(app.state.settings.database_url, original_database_url)

        rows = await self.rows(
            """
            SELECT id, username, password_algorithm, password_key_version,
                   password_nonce, password_ciphertext, password_auth_tag
            FROM outbound_proxy_configs
            WHERE user_uid=%s AND traffic_scope='account' AND account_id IS NULL
            """,
            (self.user.id,),
        )
        self.assertEqual(len(rows), 1)
        proxy_id = str(rows[0][0])
        self.assertEqual(rows[0][1], "")
        from flymail.infrastructure.security.credentials import EncryptedValue
        import base64

        def encoded(value: bytes) -> str:
            return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")

        encrypted = EncryptedValue(
            algorithm=str(rows[0][2]),
            key_version=int(rows[0][3]),
            nonce_b64=encoded(bytes(rows[0][4])),
            ciphertext_b64=encoded(bytes(rows[0][5])),
        )
        decrypted = json.loads(self.cipher.decrypt(proxy_id, encrypted))
        self.assertEqual(
            decrypted,
            {"username": proxy_username, "password": proxy_password},
        )
        rendered_database = json.dumps(rows, ensure_ascii=False, default=str)
        self.assertNotIn(proxy_username, rendered_database)
        self.assertNotIn(proxy_password, rendered_database)

    async def test_oauth_proxy_allows_public_endpoint_without_credentials(self):
        async with self.running_app() as app:
            gateway = FakeOAuthGateway()
            app.state.accounts_service.oauth_gateway = gateway
            async with self.client(app, "203.0.113.39") as client:
                csrf = await self.login(client, "account-user", "AccountPassword!123")
                saved = await client.put(
                    "/api/v2/accounts/proxy",
                    headers=self.csrf_headers(csrf),
                    json={
                        "scheme": "http",
                        "host": "8.8.8.8",
                        "port": 8080,
                    },
                )
                self.assertEqual(saved.status_code, 200)
                self.assertFalse(saved.json()["has_credentials"])

                started = await client.post(
                    "/api/v2/accounts/oauth/start",
                    headers=self.csrf_headers(csrf),
                    json={
                        "provider_key": "gmail",
                        "email": "proxyless-auth@example.com",
                        "redirect_uri": "https://testserver/api/v2/accounts/oauth/callback",
                    },
                )
                self.assertEqual(started.status_code, 201)
                self.assertEqual(
                    gateway.authorization_calls[0]["proxy_url"],
                    "http://8.8.8.8:8080",
                )

        rows = await self.rows(
            """
            SELECT username, password_algorithm, password_key_version,
                   password_nonce, password_ciphertext, password_auth_tag
            FROM outbound_proxy_configs
            WHERE user_uid=%s AND traffic_scope='account' AND account_id IS NULL
            """,
            (self.user.id,),
        )
        self.assertEqual(rows, [("", None, None, None, None, None)])

    async def test_identities_allow_primary_address_but_reject_unverified_from(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.35") as client:
                csrf = await self.login(client, "account-user", "AccountPassword!123")
                created = await client.post(
                    "/api/v2/accounts",
                    headers=self.csrf_headers(csrf),
                    json={
                        "provider_key": "qq",
                        "email": "identity@example.com",
                        "display_name": "Identity owner",
                        "credential_type": "authorization_code",
                        "credential": "identity-auth-code",
                    },
                )
                self.assertEqual(created.status_code, 201)
                account_id = str(created.json()["id"])

                listed = await client.get(f"/api/v2/accounts/{account_id}/identities")
                self.assertEqual(listed.status_code, 200)
                self.assertEqual(len(listed.json()["items"]), 1)
                default_identity = listed.json()["items"][0]
                self.assertEqual(default_identity["from_address"], "identity@example.com")
                self.assertTrue(default_identity["is_default"])
                self.assertTrue(default_identity["is_verified"])

                rejected = await client.post(
                    f"/api/v2/accounts/{account_id}/identities",
                    headers=self.csrf_headers(csrf),
                    json={
                        "from_address": "spoofed@example.net",
                        "display_name": "Spoofed",
                    },
                )
                self.assertEqual(rejected.status_code, 409)
                self.assertEqual(
                    await self.scalar(
                        "SELECT COUNT(*) FROM mail_identities WHERE account_id=%s",
                        (account_id,),
                    ),
                    1,
                )

                updated = await client.patch(
                    f"/api/v2/accounts/{account_id}/identities/{default_identity['id']}",
                    headers=self.csrf_headers(csrf),
                    json={
                        "display_name": "Updated sender",
                        "reply_to": "reply@example.com",
                        "signature_html": "<p>Safe signature</p>",
                        "signature_text": "Safe signature",
                        "is_default": True,
                    },
                )
                self.assertEqual(updated.status_code, 200)
                self.assertEqual(updated.json()["display_name"], "Updated sender")
                self.assertEqual(updated.json()["reply_to"], "reply@example.com")
                self.assertTrue(updated.json()["is_verified"])
                self.assertNotIn("credential", json.dumps(updated.json()))

    async def test_identity_partial_update_preserves_reply_signature_and_default(self):
        async with self.running_app() as app:
            app.state.accounts_service.identity_policy = lambda _account, _address: True
            async with self.client(app, "203.0.113.44") as client:
                csrf = await self.login(client, "account-user", "AccountPassword!123")
                created = await client.post(
                    "/api/v2/accounts",
                    headers=self.csrf_headers(csrf),
                    json={
                        "provider_key": "qq",
                        "email": "identity-owner@example.com",
                        "credential_type": "authorization_code",
                        "credential": "identity-owner-code",
                    },
                )
                self.assertEqual(created.status_code, 201)
                account_id = str(created.json()["id"])
                alias = await client.post(
                    f"/api/v2/accounts/{account_id}/identities",
                    headers=self.csrf_headers(csrf),
                    json={
                        "from_address": "identity-alias@example.com",
                        "display_name": "Alias",
                        "reply_to": "reply@example.com",
                        "signature_html": "<p>HTML signature</p>",
                        "signature_text": "Text signature",
                        "is_default": True,
                    },
                )
                self.assertEqual(alias.status_code, 201)
                identity_id = str(alias.json()["id"])

                updated = await client.patch(
                    f"/api/v2/accounts/{account_id}/identities/{identity_id}",
                    headers=self.csrf_headers(csrf),
                    json={"display_name": "Renamed alias"},
                )
                self.assertEqual(updated.status_code, 200)
                self.assertEqual(updated.json()["display_name"], "Renamed alias")
                self.assertEqual(updated.json()["reply_to"], "reply@example.com")
                self.assertEqual(updated.json()["signature_html"], "<p>HTML signature</p>")
                self.assertEqual(updated.json()["signature_text"], "Text signature")
                self.assertTrue(updated.json()["is_default"])

                unchanged_updated_at = float(
                    await self.scalar(
                        "SELECT updated_at FROM mail_identities WHERE id=%s",
                        (identity_id,),
                    )
                )
                with patch(
                    "flymail.repositories.accounts.time",
                    SimpleNamespace(time=lambda: unchanged_updated_at),
                ):
                    unchanged = await client.patch(
                        f"/api/v2/accounts/{account_id}/identities/{identity_id}",
                        headers=self.csrf_headers(csrf),
                        json={"display_name": "Renamed alias"},
                    )
                self.assertEqual(unchanged.status_code, 200)
                self.assertEqual(unchanged.json(), updated.json())

    async def test_new_default_identity_clears_previous_default(self):
        async with self.running_app() as app:
            app.state.accounts_service.identity_policy = lambda _account, _address: True
            async with self.client(app, "203.0.113.40") as client:
                csrf = await self.login(client, "account-user", "AccountPassword!123")
                created = await client.post(
                    "/api/v2/accounts",
                    headers=self.csrf_headers(csrf),
                    json={
                        "provider_key": "qq",
                        "email": "default-owner@example.com",
                        "credential_type": "authorization_code",
                        "credential": "default-owner-code",
                    },
                )
                self.assertEqual(created.status_code, 201)
                account_id = str(created.json()["id"])

                alias = await client.post(
                    f"/api/v2/accounts/{account_id}/identities",
                    headers=self.csrf_headers(csrf),
                    json={
                        "from_address": "alias@example.com",
                        "display_name": "Alias sender",
                        "is_default": True,
                    },
                )
                self.assertEqual(alias.status_code, 201)

                listed = await client.get(f"/api/v2/accounts/{account_id}/identities")
                self.assertEqual(listed.status_code, 200)
                defaults = [item for item in listed.json()["items"] if item["is_default"]]
                self.assertEqual(len(defaults), 1)
                self.assertEqual(defaults[0]["id"], alias.json()["id"])

    async def test_oauth_state_is_pkce_single_use_session_bound_and_uses_proxy(self):
        gateway = FakeOAuthGateway()
        async with self.running_app() as app:
            app.state.accounts_service.oauth_gateway = gateway
            async with self.client(app, "203.0.113.36") as client:
                csrf = await self.login(client, "account-user", "AccountPassword!123")
                proxy = await client.put(
                    "/api/v2/accounts/proxy",
                    headers=self.csrf_headers(csrf),
                    json={
                        "scheme": "http",
                        "host": "8.8.4.4",
                        "port": 8080,
                        "username": "oauth-proxy-user",
                        "password": "oauth-proxy-password",
                    },
                )
                self.assertEqual(proxy.status_code, 200)

                started = await client.post(
                    "/api/v2/accounts/oauth/start",
                    headers=self.csrf_headers(csrf),
                    json={
                        "provider_key": "gmail",
                        "email": "oauth@example.com",
                        "display_name": "OAuth mailbox",
                        "redirect_uri": "https://testserver/api/v2/accounts/oauth/callback",
                    },
                )
                self.assertEqual(started.status_code, 201)
                start_payload = started.json()
                state = str(start_payload["state"])
                account_id = str(start_payload["account_id"])
                self.assertTrue(state)
                self.assertIn("https://oauth.example/authorize", start_payload["authorization_url"])
                self.assertNotIn("oauth-proxy-user", json.dumps(start_payload))
                self.assertNotIn("oauth-proxy-password", json.dumps(start_payload))

                self.assertEqual(len(gateway.authorization_calls), 1)
                authorization = gateway.authorization_calls[0]
                self.assertEqual(authorization["provider_key"], "gmail")
                self.assertIn("oauth-proxy-user", str(authorization["proxy_url"]))
                self.assertIn("oauth-proxy-password", str(authorization["proxy_url"]))

                async with self.client(app, "203.0.113.37") as other_client:
                    await self.login(other_client, "other-account-user", "OtherPassword!123")
                    cross_session = await other_client.get(
                        "/api/v2/accounts/oauth/callback",
                        params={"state": state, "code": "cross-session-code"},
                    )
                    self.assertEqual(cross_session.status_code, 404)

                completed = await client.get(
                    "/api/v2/accounts/oauth/callback",
                    params={"state": state, "code": "valid-code"},
                )
                self.assertEqual(completed.status_code, 200)
                self.assertEqual(completed.json()["account"]["id"], account_id)
                self.assertTrue(completed.json()["job_id"])
                rendered_completed = json.dumps(completed.json(), ensure_ascii=False)
                self.assertNotIn("oauth-access-token-never-return", rendered_completed)
                self.assertNotIn("oauth-refresh-token-never-return", rendered_completed)

                replay = await client.get(
                    "/api/v2/accounts/oauth/callback",
                    params={"state": state, "code": "replay-code"},
                )
                self.assertEqual(replay.status_code, 409)

                status_response = await client.get(
                    "/api/v2/accounts/oauth/status",
                    params={"state": state},
                )
                self.assertEqual(status_response.status_code, 200)
                self.assertEqual(status_response.json()["status"], "consumed")

                expired_start = await client.post(
                    "/api/v2/accounts/oauth/start",
                    headers=self.csrf_headers(csrf),
                    json={
                        "provider_key": "outlook",
                        "email": "expired-oauth@example.com",
                        "redirect_uri": "https://testserver/api/v2/accounts/oauth/callback",
                    },
                )
                self.assertEqual(expired_start.status_code, 201)
                expired_state = str(expired_start.json()["state"])
                async with self.api_pool.acquire() as connection:
                    async with connection.cursor() as cursor:
                        await cursor.execute(
                            "UPDATE oauth_authorization_states SET expires_at=0 WHERE consumed_at IS NULL"
                        )
                    await connection.commit()
                expired = await client.get(
                    "/api/v2/accounts/oauth/callback",
                    params={"state": expired_state, "code": "expired-code"},
                )
                self.assertEqual(expired.status_code, 409)

        self.assertEqual(len(gateway.exchange_calls), 1)
        exchange = gateway.exchange_calls[0]
        verifier = str(exchange["code_verifier"])
        expected_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).decode("ascii").rstrip("=")
        self.assertEqual(
            gateway.authorization_calls[0]["code_challenge"],
            expected_challenge,
        )
        self.assertIn("oauth-proxy-user", str(exchange["proxy_url"]))
        self.assertIn("oauth-proxy-password", str(exchange["proxy_url"]))

        state_rows = await self.rows(
            """
            SELECT state_hash, pkce_ciphertext, consumed_at
            FROM oauth_authorization_states
            ORDER BY created_at, id
            """
        )
        self.assertEqual(len(state_rows), 2)
        self.assertEqual(len(str(state_rows[0][0])), 64)
        self.assertNotIn(state, json.dumps(state_rows, default=str))
        self.assertNotIn(verifier, json.dumps(state_rows, default=str))
        self.assertIsNotNone(state_rows[0][2])

        async with self.api_pool.acquire() as connection:
            encrypted = await CredentialRepository(connection).get_encrypted(
                TenantContext(self.user.id),
                account_id,
            )
        self.assertIsNotNone(encrypted)
        assert encrypted is not None
        token_payload = json.loads(self.cipher.decrypt(account_id, encrypted.value))
        self.assertEqual(token_payload["refresh_token"], "oauth-refresh-token-never-return")

    async def test_reauthorize_updates_version_and_safe_delete_waits_for_uncertain_send(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.38") as client:
                csrf = await self.login(client, "account-user", "AccountPassword!123")
                created = await client.post(
                    "/api/v2/accounts",
                    headers=self.csrf_headers(csrf),
                    json={
                        "provider_key": "qq",
                        "email": "delete@example.com",
                        "credential_type": "authorization_code",
                        "credential": "initial-authorization-code",
                    },
                )
                self.assertEqual(created.status_code, 201)
                account_id = str(created.json()["id"])

                reauthorized = await client.put(
                    f"/api/v2/accounts/{account_id}/credentials",
                    headers=self.csrf_headers(csrf),
                    json={
                        "credential_type": "authorization_code",
                        "credential": "replacement-authorization-code",
                    },
                )
                self.assertEqual(reauthorized.status_code, 202)
                self.assertTrue(reauthorized.json()["job_id"])

                wrong_confirmation = await client.request(
                    "DELETE",
                    f"/api/v2/accounts/{account_id}",
                    headers=self.csrf_headers(csrf),
                    json={"confirm_email": "wrong@example.com"},
                )
                self.assertEqual(wrong_confirmation.status_code, 409)

                async with self.api_pool.acquire() as connection:
                    await connection.begin()
                    jobs = JobRepository(connection)
                    uncertain_job = await jobs.enqueue(
                        JobSpec(
                            queue_name="interactive",
                            job_kind="send.verify",
                            user_uid=self.user.id,
                            account_id=account_id,
                            provider_key="qq",
                            payload={"draft_id": "drf_uncertain"},
                            dedupe_key="delete-test-uncertain-send",
                        )
                    )
                    sync_job = await jobs.enqueue(
                        JobSpec(
                            queue_name="background",
                            job_kind="sync.reconcile",
                            user_uid=self.user.id,
                            account_id=account_id,
                            provider_key="qq",
                            payload={"account_id": account_id},
                            dedupe_key="delete-test-sync",
                        )
                    )
                    await connection.commit()

                blocked = await client.request(
                    "DELETE",
                    f"/api/v2/accounts/{account_id}",
                    headers=self.csrf_headers(csrf),
                    json={"confirm_email": "delete@example.com"},
                )
                self.assertEqual(blocked.status_code, 409)
                self.assertEqual(
                    await self.scalar("SELECT status FROM mail_accounts WHERE id=%s", (account_id,)),
                    "pending",
                )

                async with self.api_pool.acquire() as connection:
                    async with connection.cursor() as cursor:
                        await cursor.execute(
                            "UPDATE worker_jobs SET status='succeeded', finished_at=1 WHERE id=%s",
                            (uncertain_job,),
                        )
                    await connection.commit()

                deleted = await client.request(
                    "DELETE",
                    f"/api/v2/accounts/{account_id}",
                    headers=self.csrf_headers(csrf),
                    json={"confirm_email": "delete@example.com"},
                )
                self.assertEqual(deleted.status_code, 202)
                self.assertEqual(deleted.json()["account"]["status"], "deleting")
                cleanup_job = str(deleted.json()["cleanup_job_id"])
                self.assertTrue(cleanup_job)

        async with self.api_pool.acquire() as connection:
            credential = await CredentialRepository(connection).get_encrypted(
                TenantContext(self.user.id),
                account_id,
            )
        self.assertIsNotNone(credential)
        assert credential is not None
        self.assertEqual(credential.credential_version, 2)
        self.assertEqual(
            self.cipher.decrypt(account_id, credential.value),
            b"replacement-authorization-code",
        )
        self.assertEqual(
            await self.scalar("SELECT status FROM worker_jobs WHERE id=%s", (sync_job,)),
            "cancelled",
        )
        cleanup = await self.rows(
            "SELECT user_uid, account_id, job_kind, status, payload FROM worker_jobs WHERE id=%s",
            (cleanup_job,),
        )
        self.assertEqual(cleanup[0][0:4], (self.user.id, None, "account.cleanup", "pending"))
        cleanup_payload = json.loads(cleanup[0][4]) if isinstance(cleanup[0][4], str) else cleanup[0][4]
        self.assertEqual(cleanup_payload, {"account_id": account_id})
        self.assertEqual(
            await self.scalar("SELECT status FROM account_runtime_state WHERE account_id=%s", (account_id,)),
            "disabled",
        )
        self.assertEqual(
            await self.scalar("SELECT COUNT(*) FROM mail_identities WHERE account_id=%s", (account_id,)),
            1,
        )
