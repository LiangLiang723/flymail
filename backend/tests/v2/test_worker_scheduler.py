from __future__ import annotations

import asyncio
import math
import os
import time
import unittest
from unittest.mock import patch

from flymail.infrastructure.db.migrations.runner import run_migrations
from flymail.repositories.accounts import AccountRepository
from flymail.repositories.base import AdminContext, TenantContext
from flymail.repositories.jobs import JobRepository, JobSpec
from flymail.repositories.users import UserRepository
from flymail.workers.dispatcher import JobContext, JobOutcome, WorkerDispatcher
from flymail.workers.scheduler import ClaimRequest, FairScheduler, ReadyJob
from tests.v2.mysql_test_case import MySqlIsolatedAsyncioTestCase
from v2_worker import run_worker


class FairSchedulerTests(unittest.TestCase):
    def job(
        self,
        job_id: str,
        queue: str,
        *,
        priority: int = 100,
        account_id: str | None = None,
        provider_key: str | None = None,
        account_status: str = "active",
        runtime_status: str = "normal",
        backoff_until: float = 0,
    ) -> ReadyJob:
        return ReadyJob(
            id=job_id,
            queue_name=queue,
            priority=priority,
            available_at=0,
            account_id=account_id,
            provider_key=provider_key,
            account_status=account_status,
            runtime_status=runtime_status,
            backoff_until=backoff_until,
        )

    def test_interactive_claims_before_history(self):
        scheduler = FairScheduler(global_slots=1)
        claims = scheduler.next_claims(
            (
                self.job("history-1", "history", priority=1),
                self.job("interactive-1", "interactive", priority=100),
            ),
            now=100,
        )
        self.assertEqual([claim.job_id for claim in claims], ["interactive-1"])

    def test_continuous_interactive_load_still_gives_history_bounded_share(self):
        scheduler = FairScheduler(global_slots=1)
        queues = []
        for index in range(18):
            claims = scheduler.next_claims(
                (
                    self.job(f"interactive-{index}", "interactive"),
                    self.job(f"history-{index}", "history"),
                ),
                now=100 + index,
            )
            queues.append(claims[0].queue_name)
        self.assertEqual(queues.count("history"), 2)
        self.assertLessEqual(max(
            right - left
            for left, right in zip(
                [-1] + [i for i, queue in enumerate(queues) if queue == "history"],
                [i for i, queue in enumerate(queues) if queue == "history"] + [len(queues)],
            )
        ), 9)

    def test_one_account_cannot_consume_all_global_slots(self):
        scheduler = FairScheduler(global_slots=4, per_account_limit=2)
        candidates = tuple(
            self.job(
                f"a-{index}",
                "realtime",
                account_id="acc-a",
                provider_key="gmail",
                priority=index,
            )
            for index in range(5)
        ) + tuple(
            self.job(
                f"b-{index}",
                "realtime",
                account_id="acc-b",
                provider_key="outlook",
                priority=index,
            )
            for index in range(2)
        )
        claims = scheduler.next_claims(candidates, now=100)
        self.assertEqual(len(claims), 4)
        self.assertEqual(sum(claim.account_id == "acc-a" for claim in claims), 2)
        self.assertEqual(sum(claim.account_id == "acc-b" for claim in claims), 2)

    def test_provider_cooldown_does_not_block_another_provider(self):
        scheduler = FairScheduler(global_slots=2)
        claims = scheduler.next_claims(
            (
                self.job(
                    "gmail-1",
                    "operations",
                    account_id="acc-gmail",
                    provider_key="gmail",
                ),
                self.job(
                    "outlook-1",
                    "operations",
                    account_id="acc-outlook",
                    provider_key="outlook",
                ),
            ),
            provider_cooldowns={"gmail": 200},
            now=100,
        )
        self.assertEqual([claim.job_id for claim in claims], ["outlook-1"])

    def test_disabled_auth_required_and_backoff_accounts_are_not_selected(self):
        scheduler = FairScheduler(global_slots=6)
        claims = scheduler.next_claims(
            (
                self.job(
                    "disabled",
                    "reconcile",
                    account_id="acc-disabled",
                    provider_key="qq",
                    account_status="disabled",
                ),
                self.job(
                    "auth-required",
                    "reconcile",
                    account_id="acc-auth",
                    provider_key="gmail",
                    runtime_status="auth_required",
                ),
                self.job(
                    "backoff",
                    "reconcile",
                    account_id="acc-backoff",
                    provider_key="icloud",
                    runtime_status="degraded",
                    backoff_until=101,
                ),
                self.job(
                    "ready",
                    "reconcile",
                    account_id="acc-ready",
                    provider_key="outlook",
                ),
                self.job("maintenance", "maintenance"),
            ),
            now=100,
        )
        self.assertEqual({claim.job_id for claim in claims}, {"ready", "maintenance"})

    def test_ready_job_rejects_non_finite_timestamps_and_boolean_priority(self):
        with self.assertRaises(ValueError):
            self.job("nan", "interactive", priority=1).__class__(
                id="nan", queue_name="interactive", priority=1,
                available_at=math.nan,
            )
        with self.assertRaises(TypeError):
            ReadyJob(
                id="bool", queue_name="interactive", priority=True,
                available_at=0,
            )

    def test_in_flight_counts_reduce_available_account_and_provider_slots(self):
        scheduler = FairScheduler(
            global_slots=4,
            per_account_limit=2,
            provider_limits={"gmail": 2},
        )
        in_flight = (
            ClaimRequest(
                job_id="active-1",
                queue_name="realtime",
                account_id="acc-a",
                provider_key="gmail",
            ),
        )
        claims = scheduler.next_claims(
            (
                self.job("a-1", "realtime", account_id="acc-a", provider_key="gmail"),
                self.job("a-2", "realtime", account_id="acc-a", provider_key="gmail"),
                self.job("b-1", "realtime", account_id="acc-b", provider_key="gmail"),
                self.job("c-1", "realtime", account_id="acc-c", provider_key="outlook"),
            ),
            in_flight=in_flight,
            now=100,
        )
        self.assertEqual({claim.job_id for claim in claims}, {"a-1", "c-1"})


class WorkerDispatcherTests(unittest.IsolatedAsyncioTestCase):
    def leased_job(self, *, kind: str = "test.job"):
        from flymail.repositories.jobs import LeasedJob

        return LeasedJob(
            id="job-test",
            user_uid="usr-test",
            account_id="acc-test",
            provider_key="generic",
            queue_name="interactive",
            job_kind=kind,
            priority=1,
            available_at=0,
            lease_owner="worker-test",
            lease_token="lease-test",
            lease_expires_at=100,
            attempt_count=1,
            max_attempts=3,
            dedupe_key=None,
            payload={"account_id": "acc-test"},
        )

    async def test_unknown_job_kind_fails_permanently_with_safe_error(self):
        dispatcher = WorkerDispatcher()
        outcome = await dispatcher.dispatch(
            self.leased_job(kind="unknown.kind"),
            stop_event=asyncio.Event(),
        )
        self.assertEqual(outcome.action, "fail")
        self.assertEqual(outcome.error_class, "UnknownJobKind")
        self.assertNotIn("payload", outcome.error_message.casefold())

    async def test_registered_handler_receives_context_and_immutable_payload(self):
        dispatcher = WorkerDispatcher()
        observed: list[tuple[JobContext, dict]] = []

        async def handler(context, payload):
            observed.append((context, dict(payload)))
            with self.assertRaises(TypeError):
                payload["mutate"] = True
            return JobOutcome.success()

        dispatcher.register("test.job", handler)
        with self.assertRaises(ValueError):
            dispatcher.register("test.job", handler)
        outcome = await dispatcher.dispatch(
            self.leased_job(),
            stop_event=asyncio.Event(),
        )
        self.assertEqual(outcome.action, "complete")
        self.assertEqual(observed[0][0].account_id, "acc-test")
        self.assertEqual(observed[0][1], {"account_id": "acc-test"})

    async def test_unhandled_handler_error_is_retryable_without_exception_text(self):
        dispatcher = WorkerDispatcher()

        async def handler(_context, _payload):
            raise RuntimeError("mail-password-must-not-leak")

        dispatcher.register("test.job", handler)
        outcome = await dispatcher.dispatch(
            self.leased_job(),
            stop_event=asyncio.Event(),
        )
        self.assertEqual(outcome.action, "retry")
        self.assertEqual(outcome.error_class, "RuntimeError")
        self.assertNotIn("mail-password", outcome.error_message)


class WorkerSchedulerIntegrationTests(MySqlIsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await run_migrations(self.pool)
        await self._clear_tables()
        self.tenant, self.accounts = await self._create_accounts()

    async def _clear_tables(self) -> None:
        tables = (
            "job_attempts",
            "worker_jobs",
            "account_runtime_state",
            "provider_credentials",
            "mail_identities",
            "mail_accounts",
            "user_profiles",
            "user_settings",
            "users",
        )
        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                for table in tables:
                    await cursor.execute(f"DELETE FROM {table}")
            await connection.commit()

    async def _create_accounts(self):
        async with self.pool.acquire() as connection:
            await connection.begin()
            user = await UserRepository(connection).create_user_for_admin(
                AdminContext("usr-worker-admin"),
                username="worker-scheduler-user",
                password_hash="test-password-hash",
            )
            tenant = TenantContext(user.id)
            repository = AccountRepository(connection)
            active = await repository.create_account(
                tenant,
                provider_key="gmail",
                email="active@example.com",
                status="active",
            )
            disabled = await repository.create_account(
                tenant,
                provider_key="outlook",
                email="disabled@example.com",
                status="disabled",
            )
            auth = await repository.create_account(
                tenant,
                provider_key="qq",
                email="auth@example.com",
                status="active",
            )
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO account_runtime_state (
                        account_id, user_uid, status, idle_status,
                        last_activity_at, last_change_at, next_reconcile_at,
                        failure_count, backoff_until, last_error_class,
                        last_error_message, updated_at
                    ) VALUES (%s, %s, 'auth_required', 'disconnected',
                              0, 0, 0, 1, 0, 'AuthenticationFailed', '', 0)
                    """,
                    (auth.id, tenant.user_uid),
                )
            await connection.commit()
        return tenant, {"active": active, "disabled": disabled, "auth": auth}

    async def _create_active_account(self, *, email: str, provider_key: str):
        async with self.pool.acquire() as connection:
            await connection.begin()
            account = await AccountRepository(connection).create_account(
                self.tenant,
                provider_key=provider_key,
                email=email,
                status="active",
            )
            await connection.commit()
            return account

    async def _enqueue_account_job(
        self,
        account_name: str,
        *,
        kind: str = "test.short",
        queue: str = "interactive",
        priority: int = 1,
    ) -> str:
        account = self.accounts[account_name]
        async with self.pool.acquire() as connection:
            await connection.begin()
            job_id = await JobRepository(connection).enqueue(
                JobSpec(
                    queue_name=queue,
                    job_kind=kind,
                    user_uid=self.tenant.user_uid,
                    account_id=account.id,
                    provider_key=account.provider_key,
                    priority=priority,
                    payload={"account_id": account.id},
                ),
                now=100,
            )
            await connection.commit()
            return job_id

    async def test_dedupe_key_cannot_cross_account_scope(self):
        other = await self._create_active_account(
            email="dedupe-other@example.com",
            provider_key="outlook",
        )
        async with self.pool.acquire() as connection:
            await connection.begin()
            first = await JobRepository(connection).enqueue(
                JobSpec(
                    queue_name="interactive",
                    job_kind="test.short",
                    user_uid=self.tenant.user_uid,
                    account_id=self.accounts["active"].id,
                    provider_key="gmail",
                    dedupe_key="same-scope-key",
                    payload={"account_id": self.accounts["active"].id},
                ),
                now=100,
            )
            await connection.commit()
        async with self.pool.acquire() as connection:
            await connection.begin()
            with self.assertRaises(ValueError):
                await JobRepository(connection).enqueue(
                    JobSpec(
                        queue_name="interactive",
                        job_kind="test.short",
                        user_uid=self.tenant.user_uid,
                        account_id=other.id,
                        provider_key="outlook",
                        dedupe_key="same-scope-key",
                        payload={"account_id": other.id},
                    ),
                    now=100,
                )
            await connection.rollback()
        self.assertEqual(
            await self.scalar("SELECT COUNT(*) FROM worker_jobs WHERE id = %s", (first,)),
            1,
        )

    async def test_enqueue_rejects_provider_scope_mismatch(self):
        async with self.pool.acquire() as connection:
            await connection.begin()
            with self.assertRaises(ValueError):
                await JobRepository(connection).enqueue(
                    JobSpec(
                        queue_name="interactive",
                        job_kind="test.short",
                        user_uid=self.tenant.user_uid,
                        account_id=self.accounts["active"].id,
                        provider_key="outlook",
                        payload={"account_id": self.accounts["active"].id},
                    ),
                    now=100,
                )
            await connection.rollback()

    async def test_candidate_sampling_preserves_other_accounts_providers_and_queues(self):
        other = await self._create_active_account(
            email="candidate-other@example.com",
            provider_key="outlook",
        )
        async with self.pool.acquire() as connection:
            await connection.begin()
            repository = JobRepository(connection)
            for index in range(12):
                await repository.enqueue(
                    JobSpec(
                        queue_name="interactive",
                        job_kind="test.short",
                        user_uid=self.tenant.user_uid,
                        account_id=self.accounts["active"].id,
                        provider_key="gmail",
                        priority=index,
                        payload={"account_id": self.accounts["active"].id, "index": index},
                    ),
                    now=100,
                )
            other_job = await repository.enqueue(
                JobSpec(
                    queue_name="interactive",
                    job_kind="test.short",
                    user_uid=self.tenant.user_uid,
                    account_id=other.id,
                    provider_key="outlook",
                    priority=100,
                    payload={"account_id": other.id},
                ),
                now=100,
            )
            history_job = await repository.enqueue(
                JobSpec(
                    queue_name="history",
                    job_kind="test.history",
                    user_uid=self.tenant.user_uid,
                    priority=1000,
                    payload={"scope": "history"},
                ),
                now=100,
            )
            await connection.commit()
        async with self.pool.acquire() as connection:
            candidates = await JobRepository(connection).list_ready_candidates(
                ("interactive", "history"),
                now=100,
                limit=4,
                per_account_limit=2,
                per_provider_limit=2,
            )
        candidate_ids = {candidate.id for candidate in candidates}
        self.assertIn(other_job, candidate_ids)
        self.assertIn(history_job, candidate_ids)
        self.assertLessEqual(
            sum(candidate.account_id == self.accounts["active"].id for candidate in candidates),
            2,
        )

    async def test_database_candidates_exclude_disabled_and_auth_required_accounts(self):
        active_id = await self._enqueue_account_job("active")
        await self._enqueue_account_job("disabled")
        await self._enqueue_account_job("auth")
        async with self.pool.acquire() as connection:
            candidates = await JobRepository(connection).list_ready_candidates(
                ("interactive",),
                now=100,
                limit=20,
            )
        self.assertEqual([candidate.id for candidate in candidates], [active_id])
        self.assertEqual(candidates[0].account_id, self.accounts["active"].id)
        self.assertEqual(candidates[0].provider_key, "gmail")

    async def test_disabled_user_account_job_is_not_a_candidate(self):
        async with self.pool.acquire() as connection:
            await connection.begin()
            user = await UserRepository(connection).create_user_for_admin(
                AdminContext("usr-worker-admin"),
                username="disabled-worker-user",
                password_hash="test-password-hash",
                enabled=False,
            )
            tenant = TenantContext(user.id)
            account = await AccountRepository(connection).create_account(
                tenant,
                provider_key="gmail",
                email="disabled-user@example.com",
                status="active",
            )
            job_id = await JobRepository(connection).enqueue(
                JobSpec(
                    queue_name="interactive",
                    job_kind="test.short",
                    user_uid=tenant.user_uid,
                    account_id=account.id,
                    provider_key="gmail",
                    payload={"account_id": account.id},
                ),
                now=100,
            )
            await connection.commit()
        async with self.pool.acquire() as connection:
            candidates = await JobRepository(connection).list_ready_candidates(
                ("interactive",), now=100, limit=20
            )
        self.assertNotIn(job_id, {candidate.id for candidate in candidates})

    async def test_claim_ids_rechecks_account_state_before_leasing(self):
        job_id = await self._enqueue_account_job("active")
        async with self.pool.acquire() as connection:
            candidates = await JobRepository(connection).list_ready_candidates(
                ("interactive",), now=100, limit=10
            )
        self.assertEqual([candidate.id for candidate in candidates], [job_id])
        async with self.pool.acquire() as connection:
            await connection.begin()
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "UPDATE mail_accounts SET status = 'disabled' WHERE id = %s",
                    (self.accounts["active"].id,),
                )
            await connection.commit()
        async with self.pool.acquire() as connection:
            await connection.begin()
            leased = await JobRepository(connection).claim_ids(
                (job_id,),
                "worker-test",
                lease_seconds=60,
                now=100,
            )
            await connection.commit()
        self.assertEqual(leased, [])
        self.assertEqual(await self.scalar("SELECT status FROM worker_jobs WHERE id = %s", (job_id,)), "pending")

    async def test_graceful_shutdown_stops_claims_and_allows_short_job_to_finish(self):
        first = await self._enqueue_account_job("active", priority=1)
        second = await self._enqueue_account_job("active", priority=2)
        started = asyncio.Event()
        release = asyncio.Event()
        stop = asyncio.Event()
        dispatcher = WorkerDispatcher()

        async def short_handler(_context, _payload):
            started.set()
            await release.wait()
            return JobOutcome.success()

        dispatcher.register("test.short", short_handler)
        scheduler = FairScheduler(global_slots=1, per_account_limit=1)
        env = {
            "DATABASE_URL": self.database_url(),
            "FLYMAIL_DATA_DIR": "/tmp/flymail-v2-worker-scheduler",
            "FLYMAIL_SESSION_SECRET": "worker-scheduler-session-secret",
        }
        with patch.dict(os.environ, env, clear=False):
            task = asyncio.create_task(
                run_worker(
                    stop_event=stop,
                    dispatcher=dispatcher,
                    scheduler=scheduler,
                    poll_seconds=0.01,
                    shutdown_grace_seconds=1,
                )
            )
            await asyncio.wait_for(started.wait(), timeout=2)
            stop.set()
            release.set()
            await asyncio.wait_for(task, timeout=3)
        rows = await self.rows(
            "SELECT id, status FROM worker_jobs WHERE id IN (%s, %s) ORDER BY priority",
            (first, second),
        )
        self.assertEqual(rows, [(first, "succeeded"), (second, "pending")])

    async def test_infrastructure_failure_releases_lease_before_propagating(self):
        job_id = await self._enqueue_account_job("active", kind="test.infrastructure")
        dispatched = asyncio.Event()
        dispatcher = WorkerDispatcher()

        async def handler(_context, _payload):
            dispatched.set()
            return JobOutcome.success()

        dispatcher.register("test.infrastructure", handler)
        env = {
            "DATABASE_URL": self.database_url(),
            "FLYMAIL_DATA_DIR": "/tmp/flymail-v2-worker-scheduler-failure",
            "FLYMAIL_SESSION_SECRET": "worker-scheduler-session-secret",
        }
        async def broken_complete(repository, *args, **kwargs):
            del repository, args, kwargs
            raise RuntimeError("simulated completion storage failure")

        with patch.dict(os.environ, env, clear=False), patch.object(
            JobRepository, "complete", broken_complete
        ):
            with self.assertRaises(RuntimeError):
                await asyncio.wait_for(
                    run_worker(
                        dispatcher=dispatcher,
                        scheduler=FairScheduler(global_slots=1),
                        poll_seconds=0.01,
                        shutdown_grace_seconds=0.05,
                    ),
                    timeout=3,
                )
        self.assertTrue(dispatched.is_set())
        row = await self.row(
            "SELECT status, lease_owner, lease_token, last_error_class FROM worker_jobs WHERE id = %s",
            (job_id,),
        )
        self.assertEqual(row, ("retry_wait", "", None, "WorkerShutdown"))

    async def test_shutdown_timeout_cancels_handler_and_releases_worker_lease(self):
        job_id = await self._enqueue_account_job("active", kind="test.long")
        started = asyncio.Event()
        stop = asyncio.Event()
        dispatcher = WorkerDispatcher()

        async def long_handler(_context, _payload):
            started.set()
            await asyncio.Event().wait()
            return JobOutcome.success()

        dispatcher.register("test.long", long_handler)
        env = {
            "DATABASE_URL": self.database_url(),
            "FLYMAIL_DATA_DIR": "/tmp/flymail-v2-worker-scheduler-timeout",
            "FLYMAIL_SESSION_SECRET": "worker-scheduler-session-secret",
        }
        with patch.dict(os.environ, env, clear=False):
            task = asyncio.create_task(
                run_worker(
                    stop_event=stop,
                    dispatcher=dispatcher,
                    scheduler=FairScheduler(global_slots=1),
                    poll_seconds=0.01,
                    shutdown_grace_seconds=0.05,
                )
            )
            await asyncio.wait_for(started.wait(), timeout=2)
            stop.set()
            await asyncio.wait_for(task, timeout=3)
        row = await self.row(
            """
            SELECT status, lease_owner, lease_token, last_error_class
            FROM worker_jobs WHERE id = %s
            """,
            (job_id,),
        )
        self.assertEqual(row[0], "retry_wait")
        self.assertEqual(row[1], "")
        self.assertIsNone(row[2])
        self.assertEqual(row[3], "WorkerShutdown")

    async def rows(self, sql: str, params: tuple = ()) -> list[tuple]:
        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(sql, params)
                return list(await cursor.fetchall())

    async def row(self, sql: str, params: tuple = ()) -> tuple:
        rows = await self.rows(sql, params)
        if not rows:
            raise AssertionError("expected one row")
        return rows[0]


if __name__ == "__main__":
    unittest.main()
