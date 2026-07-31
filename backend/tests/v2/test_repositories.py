from __future__ import annotations

import unittest

from flymail.domain.errors import ConflictError, NotFoundError
from flymail.infrastructure.db.migrations.runner import run_migrations
from flymail.infrastructure.security.credentials import CredentialCipher
from flymail.repositories.accounts import (
    AccountRepository,
    CredentialRepository,
    IdentityRepository,
)
from flymail.repositories.base import AdminContext, TenantContext, normalize_email
from flymail.repositories.settings import (
    DEFAULT_ATTACHMENT_CACHE_QUOTA_BYTES,
    DEFAULT_BODY_CACHE_QUOTA_BYTES,
    MIN_ATTACHMENT_CACHE_QUOTA_BYTES,
    SettingsRepository,
)
from flymail.repositories.users import UserRepository
from tests.v2.mysql_test_case import MySqlIsolatedAsyncioTestCase


class TenantRepositoryTests(MySqlIsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await run_migrations(self.pool)
        await self._clear_identity_tables()
        self.admin = AdminContext("usr_admin_actor")
        self.user_a = await self._create_user("alice")
        self.user_b = await self._create_user("bob")
        self.tenant_a = TenantContext(self.user_a.id)
        self.tenant_b = TenantContext(self.user_b.id)

    async def _clear_identity_tables(self) -> None:
        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                for table in (
                    "provider_credentials",
                    "mail_identities",
                    "mail_accounts",
                    "user_settings",
                    "user_profiles",
                    "users",
                ):
                    await cursor.execute(f"DELETE FROM {table}")
            await connection.commit()

    async def _create_user(self, username: str):
        async with self.pool.acquire() as connection:
            repository = UserRepository(connection)
            await connection.begin()
            user = await repository.create_user_for_admin(
                self.admin,
                username=username,
                password_hash=f"hash-for-{username}",
            )
            await connection.commit()
            return user

    async def _create_account(
        self,
        tenant: TenantContext,
        email: str,
        *,
        status: str = "active",
    ):
        async with self.pool.acquire() as connection:
            repository = AccountRepository(connection)
            await connection.begin()
            account = await repository.create_account(
                tenant,
                provider_key="custom_imap",
                email=email,
                status=status,
            )
            await connection.commit()
            return account

    async def test_cross_user_reads_are_indistinguishable_from_missing_resources(self):
        account_b = await self._create_account(self.tenant_b, "bob@example.com")
        cipher = CredentialCipher.from_master_secret("repository-test-master-secret")
        encrypted = cipher.encrypt(account_b.id, b"bob-mail-password")

        async with self.pool.acquire() as connection:
            identity_repository = IdentityRepository(connection)
            credential_repository = CredentialRepository(connection)
            await connection.begin()
            await identity_repository.create_identity(
                self.tenant_b,
                account_b.id,
                from_address="Bob@Example.com",
                display_name="Bob",
                is_default=True,
            )
            await credential_repository.store_encrypted(
                self.tenant_b,
                account_b.id,
                credential_type="password",
                value=encrypted,
            )
            await connection.commit()

        async with self.pool.acquire() as connection:
            accounts = AccountRepository(connection)
            identities = IdentityRepository(connection)
            credentials = CredentialRepository(connection)

            self.assertIsNone(await accounts.get_account(self.tenant_a, account_b.id))
            self.assertIsNone(await accounts.get_account(self.tenant_a, "acc_missing"))
            self.assertEqual(await identities.list_identities(self.tenant_a, account_b.id), [])
            self.assertEqual(await identities.list_identities(self.tenant_a, "acc_missing"), [])
            self.assertIsNone(await credentials.get_encrypted(self.tenant_a, account_b.id))
            self.assertIsNone(await credentials.get_encrypted(self.tenant_a, "acc_missing"))

            errors = []
            for account_id in (account_b.id, "acc_missing"):
                with self.assertRaises(NotFoundError) as captured:
                    await identities.create_identity(
                        self.tenant_a,
                        account_id,
                        from_address="guess@example.com",
                    )
                errors.append((type(captured.exception), str(captured.exception)))
            self.assertEqual(errors[0], errors[1])

    async def test_settings_update_is_scoped_to_current_tenant(self):
        async with self.pool.acquire() as connection:
            settings = SettingsRepository(connection)
            before_b = await settings.get_settings(self.tenant_b)
            await connection.begin()
            updated = await settings.update_settings(
                self.tenant_a,
                theme="dark",
                density="compact",
                attachment_cache_quota_bytes=0,
            )
            await connection.commit()
            after_a = await settings.get_settings(self.tenant_a)
            after_b = await settings.get_settings(self.tenant_b)

        self.assertTrue(updated)
        self.assertEqual((after_a.theme, after_a.density, after_a.attachment_cache_quota_bytes), ("dark", "compact", 0))
        self.assertEqual(after_b, before_b)

    async def test_disabled_accounts_and_users_remain_manageable_but_are_hidden_from_worker_query(self):
        active = await self._create_account(self.tenant_a, "active@example.com", status="active")
        disabled = await self._create_account(self.tenant_a, "disabled@example.com", status="disabled")
        disabled_user_account = await self._create_account(
            self.tenant_b,
            "disabled-user@example.com",
            status="active",
        )

        async with self.pool.acquire() as connection:
            users = UserRepository(connection)
            await connection.begin()
            self.assertTrue(
                await users.set_enabled_for_admin(self.admin, self.user_b.id, False)
            )
            await connection.commit()
            repository = AccountRepository(connection)
            managed = await repository.list_accounts(self.tenant_a)
            managed_disabled_user = await repository.list_accounts(self.tenant_b)
            worker_accounts = await repository.list_active_accounts_for_worker()

        worker_ids = {item.id for item in worker_accounts}
        self.assertEqual({item.id for item in managed}, {active.id, disabled.id})
        self.assertEqual({item.id for item in managed_disabled_user}, {disabled_user_account.id})
        self.assertIn(active.id, worker_ids)
        self.assertNotIn(disabled.id, worker_ids)
        self.assertNotIn(disabled_user_account.id, worker_ids)

    async def test_account_and_identity_uniqueness_use_trimmed_unicode_casefold(self):
        account = await self._create_account(self.tenant_a, "User@Example.COM")
        self.assertEqual(account.normalized_email, "user@example.com")
        self.assertEqual(normalize_email("  User@Example.COM  "), "user@example.com")

        async with self.pool.acquire() as connection:
            accounts = AccountRepository(connection)
            identities = IdentityRepository(connection)
            await connection.begin()
            with self.assertRaises(ConflictError):
                await accounts.create_account(
                    self.tenant_a,
                    provider_key="custom_imap",
                    email=" user@example.com ",
                )
            await connection.rollback()

            await connection.begin()
            first = await identities.create_identity(
                self.tenant_a,
                account.id,
                from_address="Alias@Example.com",
            )
            await connection.commit()
            self.assertEqual(first.normalized_from_address, "alias@example.com")

            await connection.begin()
            with self.assertRaises(ConflictError):
                await identities.create_identity(
                    self.tenant_a,
                    account.id,
                    from_address=" alias@example.COM ",
                )
            await connection.rollback()

        same_email_other_user = await self._create_account(self.tenant_b, "user@example.com")
        self.assertNotEqual(account.id, same_email_other_user.id)

    async def test_credential_repository_returns_encrypted_value_only(self):
        account = await self._create_account(self.tenant_a, "secure@example.com")
        cipher = CredentialCipher.from_master_secret("repository-test-master-secret", key_version=3)
        encrypted = cipher.encrypt(account.id, b"mail-secret-value")

        async with self.pool.acquire() as connection:
            repository = CredentialRepository(connection)
            await connection.begin()
            await repository.store_encrypted(
                self.tenant_a,
                account.id,
                credential_type="oauth",
                value=encrypted,
                expires_at=12345,
            )
            await connection.commit()
            record = await repository.get_encrypted(self.tenant_a, account.id)

        self.assertIsNotNone(record)
        self.assertEqual(record.value.algorithm, "AES-256-GCM")
        self.assertEqual(record.value.key_version, 3)
        self.assertEqual(cipher.decrypt(account.id, record.value), b"mail-secret-value")
        self.assertFalse(hasattr(record, "plaintext"))
        self.assertNotIn("mail-secret-value", repr(record))
        self.assertNotIn(record.value.ciphertext_b64, repr(record))

    async def test_new_user_settings_defaults_and_attachment_quota_validation(self):
        async with self.pool.acquire() as connection:
            repository = SettingsRepository(connection)
            defaults = await repository.get_settings(self.tenant_a)

            self.assertEqual(defaults.body_cache_quota_bytes, DEFAULT_BODY_CACHE_QUOTA_BYTES)
            self.assertEqual(defaults.attachment_cache_quota_bytes, DEFAULT_ATTACHMENT_CACHE_QUOTA_BYTES)
            self.assertEqual((defaults.theme, defaults.density), ("system", "comfortable"))

            await connection.begin()
            with self.assertRaisesRegex(ValueError, "100 MB"):
                await repository.update_settings(
                    self.tenant_a,
                    attachment_cache_quota_bytes=MIN_ATTACHMENT_CACHE_QUOTA_BYTES - 1,
                )
            await connection.rollback()

            await connection.begin()
            self.assertTrue(
                await repository.update_settings(
                    self.tenant_a,
                    attachment_cache_quota_bytes=MIN_ATTACHMENT_CACHE_QUOTA_BYTES,
                )
            )
            await connection.commit()

            await connection.begin()
            self.assertFalse(
                await repository.update_settings(
                    TenantContext("usr_missing"),
                    theme="dark",
                )
            )
            await connection.commit()
            self.assertEqual(
                await self.scalar(
                    "SELECT COUNT(*) FROM user_settings WHERE user_uid = %s",
                    ("usr_missing",),
                ),
                0,
            )

    async def test_repositories_do_not_commit_business_mutations(self):
        async with self.pool.acquire() as connection:
            repository = AccountRepository(connection)
            await connection.begin()
            account = await repository.create_account(
                self.tenant_a,
                provider_key="custom_imap",
                email="rollback@example.com",
            )
            await connection.rollback()

        async with self.pool.acquire() as connection:
            self.assertIsNone(await AccountRepository(connection).get_account(self.tenant_a, account.id))

    async def test_explicit_admin_methods_can_read_users_without_exposing_password_hashes(self):
        async with self.pool.acquire() as connection:
            repository = UserRepository(connection)
            tenant_user = await repository.get_user(self.tenant_a)
            admin_user = await repository.get_user_for_admin(self.admin, self.user_b.id)
            self.assertEqual(tenant_user.id, self.user_a.id)
            self.assertEqual(admin_user.id, self.user_b.id)
            self.assertFalse(hasattr(tenant_user, "password_hash"))
            self.assertFalse(hasattr(admin_user, "password_hash"))
            self.assertIsNone(await repository.get_user_for_admin(self.admin, "usr_missing"))


if __name__ == "__main__":
    unittest.main()
