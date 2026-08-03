from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

import httpx

from flymail.config import FlyMailSettings
from flymail.repositories.audit import AuditRepository
from flymail.infrastructure.db.migrations.runner import run_migrations
from flymail.infrastructure.security.passwords import hash_password
from flymail.repositories.base import AdminContext
from flymail.repositories.users import UserRepository
from tests.v2.mysql_test_case import MySqlIsolatedAsyncioTestCase
from flymail.api.app import create_app


SESSION_COOKIE_NAME = "flymail_v2_session"
ORIGIN = "https://testserver"


class AuthAdminApiTests(MySqlIsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await run_migrations(self.api_pool)
        await self._clear_tables()
        self.temp_dir = tempfile.TemporaryDirectory(prefix="flymail-v2-auth-api-")
        root = Path(self.temp_dir.name)
        self.settings = FlyMailSettings(
            role="api",
            database_url=self.database_url(),
            data_dir=root,
            object_dir=root / "objects" / "sha256",
            object_tmp_dir=root / "objects" / ".tmp",
            session_secret="auth-api-session-secret-0123456789abcdef",
            db_pool_name="flymail-api",
            db_min_connections=2,
            db_max_connections=12,
        )
        self.admin = await self._create_user(
            "admin-user",
            "AdminPassword!123",
            role="admin",
        )
        self.user = await self._create_user(
            "normal-user",
            "UserPassword!123",
        )
        self.other = await self._create_user(
            "other-user",
            "OtherPassword!123",
        )
        self.disabled = await self._create_user(
            "disabled-user",
            "DisabledPassword!123",
            enabled=False,
        )

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()
        await super().asyncTearDown()

    async def _clear_tables(self) -> None:
        tables = (
            "audit_events",
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

    async def _create_user(
        self,
        username: str,
        password: str,
        *,
        role: str = "user",
        enabled: bool = True,
    ):
        async with self.api_pool.acquire() as connection:
            await connection.begin()
            user = await UserRepository(connection).create_user_for_admin(
                AdminContext("usr_test_admin"),
                username=username,
                password_hash=hash_password(password),
                role=role,
                enabled=enabled,
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

    async def login(
        self,
        client: httpx.AsyncClient,
        username: str,
        password: str,
    ) -> httpx.Response:
        return await client.post(
            "/api/v2/auth/login",
            json={"username": username, "password": password},
        )

    @staticmethod
    def csrf_headers(csrf_token: str, *, origin: str = ORIGIN) -> dict[str, str]:
        return {
            "Origin": origin,
            "X-CSRF-Token": csrf_token,
        }

    async def rows(self, sql: str, params: tuple | list = ()) -> list[tuple]:
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(sql, params)
                return list(await cursor.fetchall())

    async def audit_rows(self) -> list[tuple]:
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT event_type, user_uid, actor_user_uid,
                           result_code, request_id, safe_metadata
                    FROM audit_events
                    ORDER BY created_at, id
                    """
                )
                return list(await cursor.fetchall())

    async def test_http_login_sets_non_secure_cookie_and_session_is_reusable(self):
        async with self.running_app() as app:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(
                    app=app,
                    raise_app_exceptions=False,
                    client=("192.168.3.53", 36080),
                ),
                base_url="http://192.168.3.53:36080",
            ) as client:
                response = await self.login(
                    client,
                    "normal-user",
                    "UserPassword!123",
                )
                self.assertEqual(response.status_code, 200)
                cookie_header = response.headers["set-cookie"]
                self.assertIn(f"{SESSION_COOKIE_NAME}=", cookie_header)
                self.assertIn("HttpOnly", cookie_header)
                self.assertNotIn("Secure", cookie_header)
                self.assertTrue(client.cookies.get(SESSION_COOKIE_NAME))

                me = await client.get("/api/v2/auth/me")
                self.assertEqual(me.status_code, 200)
                self.assertEqual(me.json()["user"]["id"], self.user.id)

                logout = await client.post(
                    "/api/v2/auth/logout",
                    headers=self.csrf_headers(
                        response.json()["csrf_token"],
                        origin="http://192.168.3.53:36080",
                    ),
                )
                self.assertEqual(logout.status_code, 200)
                self.assertNotIn("Secure", logout.headers["set-cookie"])
                self.assertIsNone(client.cookies.get(SESSION_COOKIE_NAME))
                self.assertEqual(
                    (await client.get("/api/v2/auth/me")).status_code,
                    401,
                )

    async def test_login_sets_secure_cookie_and_database_stores_only_hashes(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.10") as client:
                response = await self.login(
                    client,
                    "normal-user",
                    "UserPassword!123",
                )
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertEqual(payload["user"]["id"], self.user.id)
                self.assertEqual(payload["user"]["role"], "user")
                self.assertTrue(payload["csrf_token"])
                cookie_header = response.headers["set-cookie"]
                self.assertIn(f"{SESSION_COOKIE_NAME}=", cookie_header)
                self.assertIn("HttpOnly", cookie_header)
                self.assertIn("Secure", cookie_header)
                self.assertIn("samesite=lax", cookie_header.casefold())
                cookie_value = client.cookies.get(SESSION_COOKIE_NAME)
                self.assertTrue(cookie_value)

                me = await client.get("/api/v2/auth/me")
                self.assertEqual(me.status_code, 200)
                self.assertEqual(me.json()["user"]["id"], self.user.id)
                self.assertEqual(me.json()["csrf_token"], payload["csrf_token"])

        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT token_hash, csrf_token_hash, password_version,
                           revoked_at, expires_at
                    FROM user_sessions
                    WHERE user_uid = %s
                    """,
                    (self.user.id,),
                )
                row = await cursor.fetchone()
        self.assertEqual(len(str(row[0])), 64)
        self.assertEqual(len(str(row[1])), 64)
        self.assertEqual(int(row[2]), 1)
        self.assertIsNone(row[3])
        self.assertGreater(float(row[4]), 0)
        self.assertNotIn(str(cookie_value), json.dumps(row))
        self.assertNotIn("UserPassword!123", json.dumps(row))

        audits = await self.audit_rows()
        self.assertEqual(audits[-1][0:4], ("auth.login", self.user.id, self.user.id, "success"))
        rendered = json.dumps(audits, ensure_ascii=False, default=str)
        self.assertNotIn("UserPassword!123", rendered)
        self.assertNotIn(str(cookie_value), rendered)
        self.assertNotIn(payload["csrf_token"], rendered)

    async def test_tampered_cookie_and_changed_signing_secret_are_rejected(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.15") as client:
                login = await self.login(client, "normal-user", "UserPassword!123")
                self.assertEqual(login.status_code, 200)
                cookie_value = client.cookies.get(SESSION_COOKIE_NAME)
                self.assertTrue(cookie_value)
                client.cookies.clear()
                client.cookies.set(
                    SESSION_COOKIE_NAME,
                    str(cookie_value)[:-1] + ("A" if str(cookie_value)[-1] != "A" else "B"),
                    domain="testserver",
                    path="/",
                )
                self.assertEqual((await client.get("/api/v2/auth/me")).status_code, 401)

        rotated = FlyMailSettings(
            role="api",
            database_url=self.settings.database_url,
            data_dir=self.settings.data_dir,
            object_dir=self.settings.object_dir,
            object_tmp_dir=self.settings.object_tmp_dir,
            session_secret="rotated-auth-session-secret-0123456789abcdef",
            db_pool_name="flymail-api",
            db_min_connections=2,
            db_max_connections=12,
        )
        rotated_app = create_app(rotated)
        async with rotated_app.router.lifespan_context(rotated_app):
            async with self.client(rotated_app, "203.0.113.15") as client:
                client.cookies.clear()
                client.cookies.set(
                    SESSION_COOKIE_NAME,
                    str(cookie_value),
                    domain="testserver",
                    path="/",
                )
                response = await client.get("/api/v2/auth/me")
        self.assertEqual(response.status_code, 401)

    async def test_login_audit_failure_rolls_back_session_and_rate_window(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.16") as client:
                with patch.object(
                    AuditRepository,
                    "append",
                    side_effect=RuntimeError("audit storage failed"),
                ):
                    response = await self.login(
                        client,
                        "normal-user",
                        "UserPassword!123",
                    )
        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            await self.scalar("SELECT COUNT(*) FROM user_sessions"),
            0,
        )
        self.assertEqual(
            await self.scalar("SELECT COUNT(*) FROM login_rate_limits"),
            1,
        )

    async def test_password_changed_during_verification_never_creates_session(self):
        async def rotate_password_before_session(_function, *_args):
            async with self.api_pool.acquire() as connection:
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        UPDATE users
                        SET password_hash = %s,
                            password_version = password_version + 1,
                            updated_at = updated_at + 1
                        WHERE id = %s
                        """,
                        (hash_password("RotatedPassword!123"), self.user.id),
                    )
                await connection.commit()
            return True

        async with self.running_app() as app:
            async with self.client(app, "203.0.113.18") as client:
                with patch(
                    "flymail.application.auth.asyncio.to_thread",
                    new=rotate_password_before_session,
                ):
                    response = await self.login(
                        client,
                        "normal-user",
                        "UserPassword!123",
                    )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "invalid_credentials")
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM user_sessions"), 0)
        audits = await self.audit_rows()
        self.assertEqual(audits[-1][0], "auth.login")
        self.assertEqual(audits[-1][3], "invalid_credentials")

    async def test_concurrent_invalid_logins_share_one_bounded_window(self):
        async with self.running_app() as app:
            async def fail_once(index: int) -> int:
                async with self.client(app, "203.0.113.17") as client:
                    response = await self.login(
                        client,
                        "normal-user",
                        f"WrongPassword!{index}",
                    )
                    return response.status_code

            statuses = await asyncio.gather(*(fail_once(index) for index in range(6)))

        self.assertIn(429, statuses)
        row = await self.rows(
            """
            SELECT failure_count, blocked_until
            FROM login_rate_limits
            """
        )
        self.assertEqual(len(row), 1)
        self.assertGreaterEqual(int(row[0][0]), 5)
        self.assertGreater(float(row[0][1]), 0)

    async def test_invalid_unknown_disabled_and_wrong_password_are_indistinguishable(self):
        async with self.running_app() as app:
            responses: list[httpx.Response] = []
            cases = (
                ("missing-user", "WrongPassword!123", "203.0.113.11"),
                ("normal-user", "WrongPassword!123", "203.0.113.12"),
                ("disabled-user", "DisabledPassword!123", "203.0.113.13"),
            )
            for username, password, source in cases:
                async with self.client(app, source) as client:
                    responses.append(await self.login(client, username, password))

        signatures = {
            (response.status_code, response.json()["error"]["code"], response.json()["error"]["message"])
            for response in responses
        }
        self.assertEqual(len(signatures), 1)
        self.assertEqual(next(iter(signatures))[0], 401)
        rendered = json.dumps(await self.audit_rows(), ensure_ascii=False, default=str)
        for value in (
            "missing-user",
            "normal-user",
            "disabled-user",
            "WrongPassword!123",
            "DisabledPassword!123",
        ):
            self.assertNotIn(value, rendered)

    async def test_csrf_and_same_origin_are_required_before_logout(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.14") as client:
                login = await self.login(client, "normal-user", "UserPassword!123")
                csrf_token = login.json()["csrf_token"]

                missing = await client.post("/api/v2/auth/logout")
                wrong_origin = await client.post(
                    "/api/v2/auth/logout",
                    headers=self.csrf_headers(
                        csrf_token,
                        origin="https://evil.example",
                    ),
                )
                self.assertEqual(missing.status_code, 403)
                self.assertEqual(wrong_origin.status_code, 403)
                self.assertEqual(
                    await self.scalar(
                        "SELECT COUNT(*) FROM user_sessions WHERE user_uid=%s AND revoked_at IS NULL",
                        (self.user.id,),
                    ),
                    1,
                )

                valid = await client.post(
                    "/api/v2/auth/logout",
                    headers=self.csrf_headers(csrf_token),
                )
                self.assertEqual(valid.status_code, 200)
                self.assertEqual(valid.json(), {"ok": True})
                self.assertEqual(
                    await self.scalar(
                        "SELECT COUNT(*) FROM user_sessions WHERE user_uid=%s AND revoked_at IS NULL",
                        (self.user.id,),
                    ),
                    0,
                )
                self.assertEqual((await client.get("/api/v2/auth/me")).status_code, 401)

    async def test_password_change_can_keep_or_revoke_other_sessions(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.20") as first, self.client(
                app, "203.0.113.21"
            ) as second:
                first_login = await self.login(first, "normal-user", "UserPassword!123")
                second_login = await self.login(second, "normal-user", "UserPassword!123")
                keep = await first.post(
                    "/api/v2/auth/password",
                    headers=self.csrf_headers(first_login.json()["csrf_token"]),
                    json={
                        "current_password": "UserPassword!123",
                        "new_password": "NewPassword!456",
                        "revoke_other_sessions": False,
                    },
                )
                self.assertEqual(keep.status_code, 200)
                self.assertEqual(keep.json()["user"]["password_version"], 2)
                self.assertEqual((await first.get("/api/v2/auth/me")).status_code, 200)
                self.assertEqual((await second.get("/api/v2/auth/me")).status_code, 200)

                revoke = await first.post(
                    "/api/v2/auth/password",
                    headers=self.csrf_headers(keep.json()["csrf_token"]),
                    json={
                        "current_password": "NewPassword!456",
                        "new_password": "NewestPassword!789",
                        "revoke_other_sessions": True,
                    },
                )
                self.assertEqual(revoke.status_code, 200)
                self.assertEqual(revoke.json()["user"]["password_version"], 3)
                self.assertEqual((await first.get("/api/v2/auth/me")).status_code, 200)
                self.assertEqual((await second.get("/api/v2/auth/me")).status_code, 401)

                old_login = await self.login(second, "normal-user", "NewPassword!456")
                new_login = await self.login(second, "normal-user", "NewestPassword!789")
                self.assertEqual(old_login.status_code, 401)
                self.assertEqual(new_login.status_code, 200)

        rows = await self.rows(
            "SELECT password_version FROM user_sessions WHERE user_uid=%s ORDER BY created_at",
            (self.user.id,),
        )
        self.assertIn((3,), rows)
        self.assertEqual(
            await self.scalar("SELECT password_version FROM users WHERE id=%s", (self.user.id,)),
            3,
        )

    async def test_create_change_and_reset_accept_one_character_or_space_passwords(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.22") as admin_client, self.client(
                app, "203.0.113.23"
            ) as user_client, self.client(app, "203.0.113.24") as login_client:
                admin_login = await self.login(
                    admin_client,
                    "admin-user",
                    "AdminPassword!123",
                )
                admin_headers = self.csrf_headers(admin_login.json()["csrf_token"])

                empty_create = await admin_client.post(
                    "/api/v2/admin/users",
                    headers=admin_headers,
                    json={
                        "username": "empty-password-user",
                        "password": "",
                        "role": "user",
                        "enabled": True,
                    },
                )
                self.assertEqual(empty_create.status_code, 422)

                created = await admin_client.post(
                    "/api/v2/admin/users",
                    headers=admin_headers,
                    json={
                        "username": "short-password-user",
                        "password": "a",
                        "role": "user",
                        "enabled": True,
                    },
                )
                self.assertEqual(created.status_code, 201, created.text)
                user_id = str(created.json()["id"])

                user_login = await self.login(user_client, "short-password-user", "a")
                self.assertEqual(user_login.status_code, 200)
                empty_change = await user_client.post(
                    "/api/v2/auth/password",
                    headers=self.csrf_headers(user_login.json()["csrf_token"]),
                    json={
                        "current_password": "a",
                        "new_password": "",
                        "revoke_other_sessions": True,
                    },
                )
                self.assertEqual(empty_change.status_code, 422)

                changed = await user_client.post(
                    "/api/v2/auth/password",
                    headers=self.csrf_headers(user_login.json()["csrf_token"]),
                    json={
                        "current_password": "a",
                        "new_password": " ",
                        "revoke_other_sessions": True,
                    },
                )
                self.assertEqual(changed.status_code, 200, changed.text)
                self.assertEqual(
                    (await self.login(login_client, "short-password-user", " ")).status_code,
                    200,
                )

                empty_reset = await admin_client.post(
                    f"/api/v2/admin/users/{user_id}/reset-password",
                    headers=admin_headers,
                    json={"new_password": ""},
                )
                self.assertEqual(empty_reset.status_code, 422)
                reset = await admin_client.post(
                    f"/api/v2/admin/users/{user_id}/reset-password",
                    headers=admin_headers,
                    json={"new_password": "b"},
                )
                self.assertEqual(reset.status_code, 200, reset.text)
                login_client.cookies.clear()
                self.assertEqual(
                    (await self.login(login_client, "short-password-user", "b")).status_code,
                    200,
                )

    async def test_admin_reset_disable_enable_and_session_revoke_are_enforced(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.30") as admin_client, self.client(
                app, "203.0.113.31"
            ) as user_client:
                admin_login = await self.login(
                    admin_client,
                    "admin-user",
                    "AdminPassword!123",
                )
                user_login = await self.login(
                    user_client,
                    "normal-user",
                    "UserPassword!123",
                )
                headers = self.csrf_headers(admin_login.json()["csrf_token"])

                reset = await admin_client.post(
                    f"/api/v2/admin/users/{self.user.id}/reset-password",
                    headers=headers,
                    json={"new_password": "ResetPassword!456"},
                )
                self.assertEqual(reset.status_code, 200)
                self.assertEqual(reset.json()["password_version"], 2)
                self.assertEqual((await user_client.get("/api/v2/auth/me")).status_code, 401)
                self.assertEqual(
                    (await self.login(user_client, "normal-user", "UserPassword!123")).status_code,
                    401,
                )
                self.assertEqual(
                    (await self.login(user_client, "normal-user", "ResetPassword!456")).status_code,
                    200,
                )

                disable = await admin_client.post(
                    f"/api/v2/admin/users/{self.user.id}/disable",
                    headers=headers,
                )
                self.assertEqual(disable.status_code, 200)
                self.assertFalse(disable.json()["enabled"])
                self.assertEqual((await user_client.get("/api/v2/auth/me")).status_code, 401)
                self.assertEqual(
                    (await self.login(user_client, "normal-user", "ResetPassword!456")).status_code,
                    401,
                )

                enable = await admin_client.post(
                    f"/api/v2/admin/users/{self.user.id}/enable",
                    headers=headers,
                )
                self.assertEqual(enable.status_code, 200)
                self.assertTrue(enable.json()["enabled"])
                relogin = await self.login(user_client, "normal-user", "ResetPassword!456")
                self.assertEqual(relogin.status_code, 200)

                revoke = await admin_client.post(
                    "/api/v2/admin/sessions/revoke",
                    headers=headers,
                    json={"user_uid": self.user.id},
                )
                self.assertEqual(revoke.status_code, 200)
                self.assertGreaterEqual(revoke.json()["revoked_sessions"], 1)
                self.assertEqual((await user_client.get("/api/v2/auth/me")).status_code, 401)
                self.assertEqual((await admin_client.get("/api/v2/auth/me")).status_code, 200)

        audit_types = [row[0] for row in await self.audit_rows()]
        for expected in (
            "admin.password_reset",
            "admin.user_disabled",
            "admin.user_enabled",
            "admin.sessions_revoked",
        ):
            self.assertIn(expected, audit_types)

    async def test_normal_user_cannot_call_admin_routes_and_admin_can_create_and_list(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.40") as user_client, self.client(
                app, "203.0.113.41"
            ) as admin_client:
                user_login = await self.login(user_client, "normal-user", "UserPassword!123")
                denied = await user_client.get("/api/v2/admin/users")
                denied_create = await user_client.post(
                    "/api/v2/admin/users",
                    headers=self.csrf_headers(user_login.json()["csrf_token"]),
                    json={
                        "username": "created-user",
                        "password": "CreatedPassword!123",
                        "role": "user",
                        "enabled": True,
                    },
                )
                self.assertEqual(denied.status_code, 403)
                self.assertEqual(denied_create.status_code, 403)

                admin_login = await self.login(
                    admin_client,
                    "admin-user",
                    "AdminPassword!123",
                )
                created = await admin_client.post(
                    "/api/v2/admin/users",
                    headers=self.csrf_headers(admin_login.json()["csrf_token"]),
                    json={
                        "username": "created-user",
                        "password": "CreatedPassword!123",
                        "role": "user",
                        "enabled": True,
                    },
                )
                self.assertEqual(created.status_code, 201)
                self.assertEqual(created.json()["username"], "created-user")
                self.assertNotIn("CreatedPassword!123", created.text)
                self.assertNotIn("password_hash", created.text.casefold())
                listing = await admin_client.get("/api/v2/admin/users")
                self.assertEqual(listing.status_code, 200)
                self.assertIn("created-user", [item["username"] for item in listing.json()["items"]])

        rendered = json.dumps(await self.audit_rows(), ensure_ascii=False, default=str)
        self.assertNotIn("CreatedPassword!123", rendered)

    async def test_rate_limit_is_scoped_and_stores_only_hashed_principal_and_source(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.50") as blocked_client:
                responses = [
                    await self.login(blocked_client, "normal-user", "WrongPassword!123")
                    for _ in range(5)
                ]
                self.assertEqual(responses[-1].status_code, 429)
                async with self.api_pool.acquire() as connection:
                    async with connection.cursor() as cursor:
                        await cursor.execute("DELETE FROM login_rate_limits")
                    await connection.commit()
                local_block = await self.login(
                    blocked_client,
                    "normal-user",
                    "UserPassword!123",
                )
                self.assertEqual(local_block.status_code, 429)

            async with self.client(app, "203.0.113.51") as other_source:
                allowed_same_user = await self.login(
                    other_source,
                    "normal-user",
                    "UserPassword!123",
                )
                self.assertEqual(allowed_same_user.status_code, 200)

            async with self.client(app, "203.0.113.50") as same_source_other_user:
                allowed_other_user = await self.login(
                    same_source_other_user,
                    "other-user",
                    "OtherPassword!123",
                )
                self.assertEqual(allowed_other_user.status_code, 200)

        rows = await self.rows(
            "SELECT principal_hash, source_hash FROM login_rate_limits ORDER BY principal_hash, source_hash"
        )
        for principal_hash, source_hash in rows:
            self.assertEqual(len(str(principal_hash)), 64)
            self.assertEqual(len(str(source_hash)), 64)
        rendered = json.dumps(rows, default=str) + json.dumps(
            await self.audit_rows(),
            default=str,
        )
        for forbidden in (
            "normal-user",
            "other-user",
            "203.0.113.50",
            "203.0.113.51",
            "WrongPassword!123",
        ):
            self.assertNotIn(forbidden, rendered)


if __name__ == "__main__":
    unittest.main()
