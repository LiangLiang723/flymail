"""Tenant-safe provider account session loading and OAuth refresh."""

from __future__ import annotations

import json
import unittest

from flymail.domain.errors import UnsafeEndpointError
from flymail.infrastructure.db.migrations.runner import run_migrations
from flymail.infrastructure.security.credentials import CredentialCipher
from flymail.repositories.accounts import (
    AccountRepository,
    CredentialRepository,
    ProxyRepository,
)
from flymail.repositories.base import AdminContext, TenantContext
from flymail.repositories.users import UserRepository
from tests.v2.mysql_test_case import MySqlIsolatedAsyncioTestCase


class FakeRefreshGateway:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def refresh_token(self, **kwargs) -> dict:
        self.calls.append(dict(kwargs))
        return {
            "access_token": "refreshed-access-token",
            "refresh_token": kwargs["refresh_token"],
            "expires_at": 5000.0,
        }


class ProviderAccountRuntimeTests(MySqlIsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await run_migrations(self.api_pool)
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                for table in (
                    "outbound_proxy_configs",
                    "provider_credentials",
                    "mail_accounts",
                    "user_settings",
                    "user_profiles",
                    "users",
                ):
                    await cursor.execute(f"DELETE FROM {table}")
            await connection.commit()
        self.secret = "provider-account-runtime-master-secret"
        self.cipher = CredentialCipher.from_master_secret(self.secret)
        async with self.api_pool.acquire() as connection:
            await connection.begin()
            self.user = await UserRepository(connection).create_user_for_admin(
                AdminContext("usr_runtime_admin"),
                username="runtime-user",
                password_hash="runtime-test-hash",
            )
            await connection.commit()
        self.tenant = TenantContext(self.user.id)

    async def create_account(
        self,
        provider_key: str,
        email: str,
        credential_type: str,
        value: bytes,
        *,
        endpoint_config: dict | None = None,
    ):
        async with self.api_pool.acquire() as connection:
            await connection.begin()
            account = await AccountRepository(connection).create_account(
                self.tenant,
                provider_key=provider_key,
                email=email,
                status="active",
                endpoint_config=endpoint_config,
            )
            credential = await CredentialRepository(connection).store_encrypted(
                self.tenant,
                account.id,
                credential_type=credential_type,
                value=self.cipher.encrypt(account.id, value),
                expires_at=100.0 if credential_type == "oauth" else None,
            )
            await connection.commit()
        return account, credential

    async def test_loads_password_endpoints_and_authenticated_user_proxy(self):
        from flymail.providers.account_runtime import ProviderAccountLoader

        account, _credential = await self.create_account(
            "generic",
            "runtime@example.test",
            "password",
            b"mail-password",
            endpoint_config={
                "imap": {"host": "imap.example.test", "port": 993, "security": "tls"},
                "smtp": {"host": "smtp.example.test", "port": 587, "security": "starttls"},
            },
        )
        async with self.api_pool.acquire() as connection:
            await connection.begin()
            proxy_id = "prx_runtime_1"
            await ProxyRepository(connection).store_user_proxy(
                self.tenant,
                proxy_id=proxy_id,
                scheme="http",
                host="proxy.example.test",
                port=8080,
                value=self.cipher.encrypt(
                    proxy_id,
                    json.dumps({"username": "proxy user", "password": "p@ss/word"}).encode(),
                ),
                enabled=True,
            )
            await connection.commit()

        loaded = await ProviderAccountLoader(
            self.worker_pool,
            self.secret,
            endpoint_resolver=lambda _host, _port: ("8.8.8.8",),
            now_fn=lambda: 1000.0,
        ).load(account.id, expected_user_uid=self.user.id)
        self.assertEqual(loaded.credential.secret, "mail-password")
        self.assertEqual(loaded.endpoints.imap.host, "imap.example.test")
        self.assertEqual(loaded.endpoints.smtp.host, "smtp.example.test")
        self.assertEqual(
            loaded.proxy_url,
            "http://proxy%20user:p%40ss%2Fword@proxy.example.test:8080",
        )
        self.assertNotIn("mail-password", repr(loaded))
        self.assertNotIn("p@ss/word", repr(loaded))

    async def test_expiring_oauth_is_refreshed_and_reencrypted(self):
        from flymail.providers.account_runtime import ProviderAccountLoader

        account, old = await self.create_account(
            "gmail",
            "runtime@gmail.example.test",
            "oauth",
            json.dumps(
                {
                    "access_token": "expired-access-token",
                    "refresh_token": "refresh-token",
                    "expires_at": 100.0,
                }
            ).encode(),
        )
        gateway = FakeRefreshGateway()
        loaded = await ProviderAccountLoader(
            self.worker_pool,
            self.secret,
            oauth_gateway=gateway,
            endpoint_resolver=lambda _host, _port: ("8.8.8.8",),
            now_fn=lambda: 1000.0,
        ).load(account.id, expected_user_uid=self.user.id)
        self.assertEqual(loaded.credential.secret, "refreshed-access-token")
        self.assertEqual(gateway.calls[0]["provider_key"], "gmail")
        self.assertEqual(gateway.calls[0]["refresh_token"], "refresh-token")

        async with self.api_pool.acquire() as connection:
            current = await CredentialRepository(connection).get_encrypted(self.tenant, account.id)
        self.assertIsNotNone(current)
        assert current is not None
        self.assertGreater(current.credential_version, old.credential_version)
        persisted = json.loads(self.cipher.decrypt(account.id, current.value))
        self.assertEqual(persisted["access_token"], "refreshed-access-token")
        self.assertEqual(persisted["refresh_token"], "refresh-token")
        self.assertEqual(persisted["expires_at"], 5000.0)

    async def test_cross_tenant_expected_user_is_rejected(self):
        from flymail.providers.account_runtime import ProviderAccountLoader

        account, _credential = await self.create_account(
            "qq",
            "runtime@qq.example.test",
            "authorization_code",
            b"authorization-code",
        )
        with self.assertRaises(ValueError):
            await ProviderAccountLoader(
                self.worker_pool,
                self.secret,
                endpoint_resolver=lambda _host, _port: ("8.8.8.8",),
            ).load(
                account.id,
                expected_user_uid="usr_other_tenant",
            )

    async def test_endpoint_dns_is_revalidated_before_runtime_connection(self):
        from flymail.providers.account_runtime import ProviderAccountLoader

        account, _credential = await self.create_account(
            "generic",
            "runtime@example.test",
            "password",
            b"mail-password",
            endpoint_config={
                "imap": {"host": "mail.example.test", "port": 993, "security": "tls"},
                "smtp": {"host": "mail.example.test", "port": 587, "security": "starttls"},
            },
        )
        with self.assertRaises(UnsafeEndpointError):
            await ProviderAccountLoader(
                self.worker_pool,
                self.secret,
                endpoint_resolver=lambda _host, _port: ("127.0.0.1",),
            ).load(account.id, expected_user_uid=self.user.id)


if __name__ == "__main__":
    unittest.main()
