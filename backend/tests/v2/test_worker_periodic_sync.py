"""Database-driven periodic polling scheduler for active mail accounts."""

from __future__ import annotations

from flymail.infrastructure.db.migrations.runner import run_migrations
from flymail.repositories.accounts import AccountRepository
from flymail.repositories.base import AdminContext, TenantContext
from flymail.repositories.users import UserRepository
from tests.v2.mysql_test_case import MySqlIsolatedAsyncioTestCase


class WorkerPeriodicSyncTests(MySqlIsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await run_migrations(self.api_pool)
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                for table in (
                    "job_attempts",
                    "worker_jobs",
                    "account_runtime_state",
                    "mail_accounts",
                    "user_settings",
                    "user_profiles",
                    "users",
                ):
                    await cursor.execute(f"DELETE FROM {table}")
            await connection.commit()
        async with self.api_pool.acquire() as connection:
            await connection.begin()
            self.user = await UserRepository(connection).create_user_for_admin(
                AdminContext("usr_scheduler_admin"),
                username="scheduler-user",
                password_hash="scheduler-test-hash",
            )
            self.tenant = TenantContext(self.user.id)
            accounts = AccountRepository(connection)
            self.active = await accounts.create_account(
                self.tenant,
                provider_key="gmail",
                email="active@example.test",
                status="active",
                poll_interval_seconds=60,
            )
            self.backoff = await accounts.create_account(
                self.tenant,
                provider_key="outlook",
                email="backoff@example.test",
                status="active",
                poll_interval_seconds=120,
            )
            self.disabled = await accounts.create_account(
                self.tenant,
                provider_key="qq",
                email="disabled@example.test",
                status="disabled",
                poll_interval_seconds=60,
            )
            for account in (self.active, self.backoff, self.disabled):
                await accounts.ensure_runtime_state(
                    self.tenant,
                    account.id,
                    status="normal" if account.status == "active" else "disabled",
                )
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "UPDATE account_runtime_state SET next_reconcile_at=0, backoff_until=0 WHERE account_id=%s",
                    (self.active.id,),
                )
                await cursor.execute(
                    "UPDATE account_runtime_state SET next_reconcile_at=0, backoff_until=2000 WHERE account_id=%s",
                    (self.backoff.id,),
                )
                await cursor.execute(
                    "UPDATE account_runtime_state SET next_reconcile_at=0, backoff_until=0 WHERE account_id=%s",
                    (self.disabled.id,),
                )
            await connection.commit()

    async def test_due_accounts_enqueue_one_deduplicated_reconcile_and_advance_schedule(self):
        from flymail.workers.periodic import schedule_due_sync_jobs

        first = await schedule_due_sync_jobs(self.worker_pool, now=1000.0, limit=50)
        self.assertEqual(first, 1)
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT user_uid, account_id, provider_key, queue_name,
                           job_kind, status, dedupe_key, payload
                    FROM worker_jobs
                    ORDER BY id
                    """
                )
                jobs = list(await cursor.fetchall())
                await cursor.execute(
                    "SELECT next_reconcile_at FROM account_runtime_state WHERE account_id=%s",
                    (self.active.id,),
                )
                next_at = float((await cursor.fetchone())[0])
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0][0:6], (
            self.user.id,
            self.active.id,
            "gmail",
            "reconcile",
            "sync.reconcile",
            "pending",
        ))
        self.assertEqual(jobs[0][6], f"periodic:sync.reconcile:{self.active.id}")
        self.assertGreaterEqual(next_at, 1060.0)

        second = await schedule_due_sync_jobs(self.worker_pool, now=1000.0, limit=50)
        self.assertEqual(second, 0)
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM worker_jobs"), 1)

    async def test_backoff_disabled_and_auth_required_accounts_are_not_scheduled(self):
        from flymail.workers.periodic import schedule_due_sync_jobs

        async with self.api_pool.acquire() as connection:
            await connection.begin()
            await AccountRepository(connection).update_status(
                self.tenant,
                self.active.id,
                "auth_required",
            )
            await connection.commit()
        scheduled = await schedule_due_sync_jobs(self.worker_pool, now=1000.0, limit=50)
        self.assertEqual(scheduled, 0)
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM worker_jobs"), 0)


if __name__ == "__main__":
    import unittest

    unittest.main()
