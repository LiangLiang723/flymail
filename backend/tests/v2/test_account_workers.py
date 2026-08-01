from __future__ import annotations

import asyncio
import base64
import importlib.util
import json

from flymail.infrastructure.db.migrations.runner import run_migrations
from flymail.providers.errors import ProviderError, ProviderErrorCode
from flymail.infrastructure.security.credentials import CredentialCipher
from flymail.repositories.accounts import (
    AccountRepository,
    CredentialRepository,
    IdentityRepository,
)
from flymail.repositories.base import AdminContext, TenantContext
from flymail.repositories.jobs import JobRepository, JobSpec
from flymail.repositories.users import UserRepository
from flymail.workers.dispatcher import JobContext
from tests.v2.mysql_test_case import MySqlIsolatedAsyncioTestCase


class FakeVerificationGateway:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def verify(self, **kwargs) -> None:
        self.calls.append(dict(kwargs))


class FailingVerificationGateway:
    def __init__(self, error: ProviderError) -> None:
        self.error = error

    async def verify(self, **_kwargs) -> None:
        raise self.error


class FakeCleanupGateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def cleanup(self, *, user_uid: str, account_id: str) -> None:
        self.calls.append((user_uid, account_id))


class AccountWorkerTests(MySqlIsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await run_migrations(self.pool)
        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                for table in (
                    "job_attempts",
                    "worker_jobs",
                    "outbox_events",
                    "account_runtime_state",
                    "outbound_proxy_configs",
                    "provider_credentials",
                    "mail_identities",
                    "mail_accounts",
                    "user_settings",
                    "users",
                ):
                    await cursor.execute(f"DELETE FROM {table}")
            await connection.commit()
        async with self.pool.acquire() as connection:
            await connection.begin()
            self.user = await UserRepository(connection).create_user_for_admin(
                AdminContext("usr_account_worker_admin"),
                username="account-worker-user",
                password_hash="test-password-hash",
            )
            await connection.commit()
        self.tenant = TenantContext(self.user.id)
        self.secret = "account-worker-secret-0123456789abcdef"
        self.cipher = CredentialCipher.from_master_secret(self.secret)

    async def _create_account(
        self,
        *,
        status: str = "pending",
        email_prefix: str = "",
    ):
        async with self.pool.acquire() as connection:
            await connection.begin()
            accounts = AccountRepository(connection)
            account = await accounts.create_account(
                self.tenant,
                provider_key="generic",
                email=f"{email_prefix or status}@example.com",
                status=status,
                endpoint_config={
                    "imap": {"host": "8.8.8.8", "port": 993, "security": "tls"},
                    "smtp": {"host": "1.1.1.1", "port": 587, "security": "starttls"},
                },
            )
            await IdentityRepository(connection).create_identity(
                self.tenant,
                account.id,
                from_address=account.email,
                is_default=True,
                is_verified=True,
            )
            encrypted = self.cipher.encrypt(account.id, b"worker-mail-secret")
            credential = await CredentialRepository(connection).store_encrypted(
                self.tenant,
                account.id,
                credential_type="password",
                value=encrypted,
            )
            await accounts.ensure_runtime_state(
                self.tenant,
                account.id,
                status="disabled" if status == "deleting" else "normal",
            )
            await connection.commit()
        return account, credential

    @staticmethod
    def context(*, user_uid: str, account_id: str | None, provider_key: str | None) -> JobContext:
        return JobContext(
            job_id="job_account_worker",
            user_uid=user_uid,
            account_id=account_id,
            provider_key=provider_key,
            queue_name="interactive" if account_id else "maintenance",
            worker_id="worker-account-test",
            attempt_count=1,
            stop_event=asyncio.Event(),
        )

    async def test_verification_decrypts_only_in_worker_revalidates_endpoint_and_ignores_stale_version(self):
        self.assertIsNotNone(importlib.util.find_spec("flymail.workers.accounts"))
        from flymail.workers.accounts import AccountVerificationHandler

        account, credential = await self._create_account()
        proxy_id = "prx_account_worker"
        proxy_secret = json.dumps(
            {"username": "proxy-worker-user", "password": "proxy-worker-secret"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        encrypted_proxy = self.cipher.encrypt(proxy_id, proxy_secret)

        def decoded(value: str) -> bytes:
            return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO outbound_proxy_configs (
                        id, user_uid, account_id, traffic_scope,
                        proxy_scheme, host, port, username,
                        password_algorithm, password_key_version,
                        password_nonce, password_ciphertext, password_auth_tag,
                        enabled, created_at, updated_at
                    ) VALUES (%s, %s, NULL, 'account', 'http', '8.8.4.4', 8080, '',
                              %s, %s, %s, %s, NULL, 1, 1, 1)
                    """,
                    (
                        proxy_id,
                        self.user.id,
                        encrypted_proxy.algorithm,
                        encrypted_proxy.key_version,
                        decoded(encrypted_proxy.nonce_b64),
                        decoded(encrypted_proxy.ciphertext_b64),
                    ),
                )
            await connection.commit()
        gateway = FakeVerificationGateway()
        handler = AccountVerificationHandler(
            self.pool,
            self.secret,
            gateway,
        )
        outcome = await handler(
            self.context(
                user_uid=self.user.id,
                account_id=account.id,
                provider_key=account.provider_key,
            ),
            {
                "account_id": account.id,
                "credential_version": credential.credential_version,
            },
        )
        self.assertEqual(outcome.action, "complete")
        self.assertEqual(len(gateway.calls), 1)
        self.assertEqual(gateway.calls[0]["credential"], b"worker-mail-secret")
        self.assertEqual(gateway.calls[0]["credential_type"], "password")
        self.assertEqual(gateway.calls[0]["endpoint_config"]["imap"]["host"], "8.8.8.8")
        self.assertEqual(
            gateway.calls[0]["proxy_url"],
            "http://proxy-worker-user:proxy-worker-secret@8.8.4.4:8080",
        )
        self.assertEqual(
            await self.scalar("SELECT status FROM mail_accounts WHERE id=%s", (account.id,)),
            "active",
        )

        stale = await handler(
            self.context(
                user_uid=self.user.id,
                account_id=account.id,
                provider_key=account.provider_key,
            ),
            {
                "account_id": account.id,
                "credential_version": credential.credential_version - 1,
            },
        )
        self.assertEqual(stale.action, "complete")
        self.assertEqual(len(gateway.calls), 1)

    async def test_auth_failure_marks_account_reauthorization_required_but_network_retries(self):
        from flymail.workers.accounts import AccountVerificationHandler

        auth_account, auth_credential = await self._create_account(
            status="pending",
            email_prefix="pending-auth",
        )
        auth_handler = AccountVerificationHandler(
            self.pool,
            self.secret,
            FailingVerificationGateway(
                ProviderError(ProviderErrorCode.AUTHORIZATION_REQUIRED)
            ),
        )
        auth_outcome = await auth_handler(
            self.context(
                user_uid=self.user.id,
                account_id=auth_account.id,
                provider_key=auth_account.provider_key,
            ),
            {
                "account_id": auth_account.id,
                "credential_version": auth_credential.credential_version,
            },
        )
        self.assertEqual(auth_outcome.action, "fail")
        self.assertEqual(auth_outcome.error_class, "authorization_required")
        self.assertEqual(
            await self.scalar("SELECT status FROM mail_accounts WHERE id=%s", (auth_account.id,)),
            "auth_required",
        )
        self.assertEqual(
            await self.scalar(
                "SELECT status FROM account_runtime_state WHERE account_id=%s",
                (auth_account.id,),
            ),
            "auth_required",
        )
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM outbox_events WHERE aggregate_id=%s AND event_type='account.authorization_required'",
                (auth_account.id,),
            ),
            1,
        )

        network_account, network_credential = await self._create_account(
            status="pending",
            email_prefix="pending-network",
        )
        network_handler = AccountVerificationHandler(
            self.pool,
            self.secret,
            FailingVerificationGateway(
                ProviderError(ProviderErrorCode.CONNECTION_FAILED)
            ),
        )
        network_outcome = await network_handler(
            self.context(
                user_uid=self.user.id,
                account_id=network_account.id,
                provider_key=network_account.provider_key,
            ),
            {
                "account_id": network_account.id,
                "credential_version": network_credential.credential_version,
            },
        )
        self.assertEqual(network_outcome.action, "retry")
        self.assertEqual(network_outcome.error_class, "connection_failed")
        self.assertEqual(
            await self.scalar("SELECT status FROM mail_accounts WHERE id=%s", (network_account.id,)),
            "pending",
        )

    async def test_pending_account_verification_job_is_schedulable_but_disabled_is_not(self):
        pending, pending_credential = await self._create_account(status="pending")
        disabled, disabled_credential = await self._create_account(status="disabled")
        async with self.pool.acquire() as connection:
            await connection.begin()
            jobs = JobRepository(connection)
            pending_job = await jobs.enqueue(
                JobSpec(
                    queue_name="interactive",
                    job_kind="account.verify",
                    user_uid=self.user.id,
                    account_id=pending.id,
                    provider_key=pending.provider_key,
                    priority=0,
                    payload={
                        "account_id": pending.id,
                        "credential_version": pending_credential.credential_version,
                    },
                    dedupe_key="pending-account-verification",
                ),
                now=10,
            )
            await jobs.enqueue(
                JobSpec(
                    queue_name="interactive",
                    job_kind="account.verify",
                    user_uid=self.user.id,
                    account_id=disabled.id,
                    provider_key=disabled.provider_key,
                    priority=0,
                    payload={
                        "account_id": disabled.id,
                        "credential_version": disabled_credential.credential_version,
                    },
                    dedupe_key="disabled-account-verification",
                ),
                now=10,
            )
            await connection.commit()

        async with self.pool.acquire() as connection:
            candidates = await JobRepository(connection).list_ready_candidates(
                ("interactive",),
                now=10,
            )
        self.assertEqual([candidate.id for candidate in candidates], [pending_job])

        async with self.pool.acquire() as connection:
            await connection.begin()
            claimed = await JobRepository(connection).claim_ids(
                (pending_job,),
                "worker-account-scheduler",
                lease_seconds=60,
                now=10,
            )
            await connection.commit()
        self.assertEqual([job.id for job in claimed], [pending_job])

    async def test_cleanup_requires_deleting_status_and_finishes_through_gateway(self):
        self.assertIsNotNone(importlib.util.find_spec("flymail.workers.accounts"))
        from flymail.workers.accounts import AccountCleanupHandler

        pending, _credential = await self._create_account(status="pending")
        deleting, _deleting_credential = await self._create_account(status="deleting")
        gateway = FakeCleanupGateway()
        handler = AccountCleanupHandler(self.pool, gateway)

        rejected = await handler(
            self.context(user_uid=self.user.id, account_id=None, provider_key=None),
            {"account_id": pending.id},
        )
        self.assertEqual(rejected.action, "fail")
        self.assertEqual(gateway.calls, [])

        completed = await handler(
            self.context(user_uid=self.user.id, account_id=None, provider_key=None),
            {"account_id": deleting.id},
        )
        self.assertEqual(completed.action, "complete")
        self.assertEqual(gateway.calls, [(self.user.id, deleting.id)])
        self.assertEqual(
            await self.scalar("SELECT status FROM mail_accounts WHERE id=%s", (deleting.id,)),
            "disabled",
        )
        self.assertEqual(
            await self.scalar(
                "SELECT status FROM account_runtime_state WHERE account_id=%s",
                (deleting.id,),
            ),
            "disabled",
        )
