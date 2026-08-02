from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from flymail.infrastructure.db.migrations.runner import run_migrations
from flymail.repositories.base import TenantContext
from flymail.repositories.jobs import JobRepository, JobSpec, retry_delay_seconds
from flymail.repositories.outbox import OutboxRepository
from flymail.workers.dispatcher import JobOutcome, WorkerDispatcher
from flymail.workers.lease import WorkerHeartbeatService
from tests.v2.mysql_test_case import MySqlIsolatedAsyncioTestCase
from flymail.workers.main import run_worker


class JobsAndOutboxTests(MySqlIsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await run_migrations(self.pool)
        await self._clear_tables()
        self.tenant = TenantContext("usr_jobs_test")

    async def _clear_tables(self) -> None:
        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                for table in ("job_attempts", "worker_jobs", "outbox_events"):
                    await cursor.execute(f"DELETE FROM {table}")
            await connection.commit()

    async def _enqueue(
        self,
        *,
        queue_name: str = "sync",
        job_kind: str = "sync.account",
        dedupe_key: str | None = None,
        available_at: float = 0,
        priority: int = 100,
        max_attempts: int = 10,
        payload: dict | None = None,
    ) -> str:
        async with self.pool.acquire() as connection:
            await connection.begin()
            job_id = await JobRepository(connection).enqueue(
                JobSpec(
                    queue_name=queue_name,
                    job_kind=job_kind,
                    user_uid=self.tenant.user_uid,
                    dedupe_key=dedupe_key,
                    available_at=available_at,
                    priority=priority,
                    max_attempts=max_attempts,
                    payload=payload or {"account_id": "acc_test"},
                ),
                now=100,
            )
            await connection.commit()
            return job_id

    async def test_outbox_and_business_job_share_transaction_atomicity(self):
        async with self.pool.acquire() as connection:
            jobs = JobRepository(connection)
            outbox = OutboxRepository(connection, self.tenant, trace_id="trc_atomic")
            await connection.begin()
            rolled_back_job = await jobs.enqueue(
                JobSpec(
                    queue_name="sync",
                    job_kind="sync.account",
                    user_uid=self.tenant.user_uid,
                    payload={"account_id": "acc_rollback"},
                ),
                now=100,
            )
            rolled_back_event = await outbox.append(
                "account.sync_requested",
                "acc_rollback",
                {"account_id": "acc_rollback"},
                now=100,
            )
            await connection.rollback()

            await connection.begin()
            committed_job = await jobs.enqueue(
                JobSpec(
                    queue_name="sync",
                    job_kind="sync.account",
                    user_uid=self.tenant.user_uid,
                    payload={"account_id": "acc_commit"},
                ),
                now=101,
            )
            committed_event = await outbox.append(
                "account.sync_requested",
                "acc_commit",
                {"account_id": "acc_commit"},
                now=101,
            )
            await connection.commit()

        self.assertEqual(
            await self.scalar("SELECT COUNT(*) FROM worker_jobs WHERE id = %s", (rolled_back_job,)),
            0,
        )
        self.assertEqual(
            await self.scalar("SELECT COUNT(*) FROM outbox_events WHERE id = %s", (rolled_back_event,)),
            0,
        )
        self.assertEqual(
            await self.scalar("SELECT COUNT(*) FROM worker_jobs WHERE id = %s", (committed_job,)),
            1,
        )
        self.assertEqual(
            await self.scalar("SELECT COUNT(*) FROM outbox_events WHERE id = %s", (committed_event,)),
            1,
        )

    async def test_outbox_payload_is_versioned_and_rejects_sensitive_or_binary_values(self):
        async with self.pool.acquire() as connection:
            repository = OutboxRepository(connection, self.tenant, trace_id="trc_safe")
            await connection.begin()
            event_id = await repository.append(
                "account.updated",
                "acc_safe",
                {"changes": {"display_name": "Safe"}},
                now=123,
            )
            await connection.commit()
            async with connection.cursor() as cursor:
                await cursor.execute("SELECT payload FROM outbox_events WHERE id = %s", (event_id,))
                row = await cursor.fetchone()

            stored = row[0] if isinstance(row[0], dict) else json.loads(row[0])
            self.assertEqual(stored["schema_version"], 1)
            self.assertEqual(stored["user_uid"], self.tenant.user_uid)
            self.assertEqual(stored["trace_id"], "trc_safe")
            self.assertEqual(stored["created_at"], 123)
            self.assertEqual(stored["event"]["changes"]["display_name"], "Safe")

            for payload in (
                {"password": "hidden"},
                {"nested": {"token": "hidden"}},
                {"secret": "hidden"},
                {"authorization": "hidden"},
                {"body_html": "<p>body</p>"},
                {"attachment": b"raw-bytes"},
            ):
                with self.subTest(payload=repr(payload)):
                    with self.assertRaises(ValueError):
                        await repository.append("unsafe.event", "agg_unsafe", payload, now=123)

    async def test_two_workers_skip_locked_rows_without_duplicate_claims(self):
        for index in range(6):
            await self._enqueue(
                dedupe_key=f"claim-{index}",
                priority=index,
                payload={"sequence": index},
            )

        async with self.api_pool.acquire() as first_connection, self.worker_pool.acquire() as second_connection:
            await first_connection.begin()
            await second_connection.begin()
            first_claim, second_claim = await asyncio.gather(
                JobRepository(first_connection).claim(
                    "sync", "worker-one", limit=3, lease_seconds=30, now=200
                ),
                JobRepository(second_connection).claim(
                    "sync", "worker-two", limit=3, lease_seconds=30, now=200
                ),
            )
            await first_connection.commit()
            await second_connection.commit()

        first_ids = {job.id for job in first_claim}
        second_ids = {job.id for job in second_claim}
        self.assertEqual(len(first_ids), 3)
        self.assertEqual(len(second_ids), 3)
        self.assertFalse(first_ids & second_ids)
        self.assertEqual(len(first_ids | second_ids), 6)

    async def test_expired_lease_is_reclaimed_and_stale_token_cannot_complete(self):
        job_id = await self._enqueue(dedupe_key="lease-reclaim")

        async with self.worker_pool.acquire() as connection:
            jobs = JobRepository(connection)
            await connection.begin()
            first = (await jobs.claim("sync", "worker-old", 1, 5, now=300))[0]
            await connection.commit()

            await connection.begin()
            self.assertEqual(await jobs.release_expired_leases(now=306), 1)
            await connection.commit()

            await connection.begin()
            second = (await jobs.claim("sync", "worker-new", 1, 5, now=306))[0]
            await connection.commit()

            self.assertEqual(second.id, job_id)
            self.assertNotEqual(first.lease_token, second.lease_token)

            await connection.begin()
            self.assertFalse(await jobs.complete(job_id, first.lease_token, now=307))
            self.assertTrue(await jobs.complete(job_id, second.lease_token, now=307))
            await connection.commit()

    async def test_concurrent_enqueue_with_same_dedupe_key_creates_one_active_job(self):
        async def enqueue_once(pool, worker_label: str) -> str:
            async with pool.acquire() as connection:
                await connection.begin()
                try:
                    job_id = await JobRepository(connection).enqueue(
                        JobSpec(
                            queue_name="sync",
                            job_kind="sync.account",
                            user_uid=self.tenant.user_uid,
                            dedupe_key="concurrent-dedupe",
                            payload={"requested_by": worker_label},
                        ),
                        now=100,
                    )
                    await connection.commit()
                    return job_id
                except Exception:
                    await connection.rollback()
                    raise

        first_id, second_id = await asyncio.gather(
            enqueue_once(self.api_pool, "api"),
            enqueue_once(self.worker_pool, "worker"),
        )

        self.assertEqual(first_id, second_id)
        self.assertEqual(
            await self.scalar(
                """
                SELECT COUNT(*)
                FROM worker_jobs
                WHERE queue_name = 'sync'
                  AND dedupe_key = 'concurrent-dedupe'
                  AND status IN ('pending', 'leased', 'running', 'retry_wait')
                """
            ),
            1,
        )

    async def test_dedupe_key_reuses_active_job_and_supersedes_terminal_job(self):
        first_id = await self._enqueue(dedupe_key="same-account", payload={"version": 1})

        async with self.pool.acquire() as connection:
            jobs = JobRepository(connection)
            await connection.begin()
            duplicate_id = await jobs.enqueue(
                JobSpec(
                    queue_name="sync",
                    job_kind="sync.account",
                    user_uid=self.tenant.user_uid,
                    dedupe_key="same-account",
                    payload={"version": 2},
                ),
                now=101,
            )
            await connection.commit()
            self.assertEqual(duplicate_id, first_id)

            await connection.begin()
            leased = (await jobs.claim("sync", "worker-dedupe", 1, 30, now=102))[0]
            self.assertTrue(await jobs.complete(leased.id, leased.lease_token, now=103))
            await connection.commit()

            await connection.begin()
            reset_id = await jobs.enqueue(
                JobSpec(
                    queue_name="sync",
                    job_kind="sync.account",
                    user_uid=self.tenant.user_uid,
                    dedupe_key="same-account",
                    payload={"version": 3},
                ),
                now=104,
            )
            await connection.commit()

        self.assertNotEqual(reset_id, first_id)
        self.assertEqual(
            await self.scalar("SELECT COUNT(*) FROM worker_jobs WHERE queue_name = 'sync' AND dedupe_key = 'same-account'"),
            1,
        )
        self.assertEqual(
            await self.scalar("SELECT status FROM worker_jobs WHERE id = %s", (first_id,)),
            "succeeded",
        )
        self.assertIsNone(
            await self.scalar("SELECT dedupe_key FROM worker_jobs WHERE id = %s", (first_id,))
        )
        self.assertEqual(
            await self.scalar("SELECT status FROM worker_jobs WHERE id = %s", (reset_id,)),
            "pending",
        )

        async with self.worker_pool.acquire() as connection:
            jobs = JobRepository(connection)
            await connection.begin()
            claimed = (await jobs.claim("sync", "worker-next-generation", 1, 30, now=105))[0]
            self.assertEqual(claimed.id, reset_id)
            self.assertEqual(claimed.attempt_count, 1)
            self.assertTrue(await jobs.complete(claimed.id, claimed.lease_token, now=106))
            await connection.commit()

    async def test_heartbeat_updates_only_active_job_with_current_token(self):
        job_id = await self._enqueue(dedupe_key="heartbeat", priority=0)
        pending_id = await self._enqueue(dedupe_key="pending-heartbeat")

        async with self.worker_pool.acquire() as connection:
            jobs = JobRepository(connection)
            await connection.begin()
            leased = (await jobs.claim("sync", "worker-heartbeat", 1, 10, now=400))[0]
            await connection.commit()

            await connection.begin()
            self.assertTrue(await jobs.heartbeat(job_id, leased.lease_token, 20, now=405))
            self.assertFalse(await jobs.heartbeat(pending_id, "lease_wrong", 20, now=405))
            await connection.commit()

            self.assertEqual(
                await self.scalar("SELECT lease_expires_at FROM worker_jobs WHERE id = %s", (job_id,)),
                425,
            )

            await connection.begin()
            self.assertTrue(await jobs.complete(job_id, leased.lease_token, now=406))
            self.assertFalse(await jobs.heartbeat(job_id, leased.lease_token, 20, now=407))
            await connection.commit()

    async def test_retry_uses_bounded_exponential_backoff_and_deterministic_jitter(self):
        self.assertEqual(retry_delay_seconds(1, 10, 60, 3), 13)
        self.assertEqual(retry_delay_seconds(2, 10, 60, 3), 23)
        self.assertEqual(retry_delay_seconds(9, 10, 60, 3), 63)
        self.assertEqual(retry_delay_seconds(10**9, 10, 60, 3), 63)

        job_id = await self._enqueue(dedupe_key="retry")
        async with self.worker_pool.acquire() as connection:
            jobs = JobRepository(connection)
            await connection.begin()
            leased = (await jobs.claim("sync", "worker-retry", 1, 10, now=500))[0]
            self.assertTrue(
                await jobs.retry(
                    job_id,
                    leased.lease_token,
                    error_class="TemporaryError",
                    error_message="temporary",
                    base_seconds=10,
                    max_seconds=60,
                    jitter_seconds=3,
                    now=501,
                )
            )
            await connection.commit()

        self.assertEqual(
            await self.scalar("SELECT available_at FROM worker_jobs WHERE id = %s", (job_id,)),
            514,
        )
        self.assertEqual(
            await self.scalar("SELECT status FROM worker_jobs WHERE id = %s", (job_id,)),
            "retry_wait",
        )

    async def test_permanent_failure_and_max_attempt_retry_finish_jobs(self):
        permanent_id = await self._enqueue(dedupe_key="permanent-failure", priority=0)
        maxed_id = await self._enqueue(dedupe_key="maxed-retry", priority=1, max_attempts=1)
        async with self.worker_pool.acquire() as connection:
            jobs = JobRepository(connection)
            await connection.begin()
            permanent = (await jobs.claim("sync", "worker-fail", 1, 10, now=550))[0]
            self.assertEqual(permanent.id, permanent_id)
            self.assertTrue(
                await jobs.fail(
                    permanent.id,
                    permanent.lease_token,
                    error_class="PermanentError",
                    error_message="permanent",
                    now=551,
                )
            )
            await connection.commit()

            await connection.begin()
            maxed = (await jobs.claim("sync", "worker-maxed", 1, 10, now=552))[0]
            self.assertEqual(maxed.id, maxed_id)
            self.assertTrue(
                await jobs.retry(
                    maxed.id,
                    maxed.lease_token,
                    error_class="TemporaryError",
                    error_message="retry budget exhausted",
                    base_seconds=10,
                    max_seconds=60,
                    now=553,
                )
            )
            await connection.commit()

        self.assertEqual(
            await self.scalar("SELECT status FROM worker_jobs WHERE id = %s", (permanent_id,)),
            "failed",
        )
        self.assertEqual(
            await self.scalar("SELECT status FROM worker_jobs WHERE id = %s", (maxed_id,)),
            "failed",
        )
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM job_attempts WHERE job_id IN (%s, %s) AND outcome = 'failed'",
                (permanent_id, maxed_id),
            ),
            2,
        )

    async def test_worker_heartbeat_service_extends_only_owned_active_jobs(self):
        job_id = await self._enqueue(dedupe_key="process-heartbeat", priority=0)
        async with self.worker_pool.acquire() as connection:
            jobs = JobRepository(connection)
            await connection.begin()
            leased = (await jobs.claim("sync", "worker-process", 1, 10, now=600))[0]
            await connection.commit()

        service = WorkerHeartbeatService(self.worker_pool, now_fn=lambda: 605, lease_seconds=30)
        await service.touch("worker-process", "worker")
        self.assertEqual(
            await self.scalar("SELECT heartbeat_at FROM worker_jobs WHERE id = %s", (job_id,)),
            605,
        )
        self.assertEqual(
            await self.scalar("SELECT lease_expires_at FROM worker_jobs WHERE id = %s", (job_id,)),
            635,
        )

        async with self.worker_pool.acquire() as connection:
            jobs = JobRepository(connection)
            await connection.begin()
            self.assertTrue(await jobs.complete(job_id, leased.lease_token, now=606))
            await connection.commit()
        await service.touch("worker-process", "worker")
        self.assertIsNone(
            await self.scalar("SELECT heartbeat_at FROM worker_jobs WHERE id = %s", (job_id,))
        )

    async def test_worker_cli_handles_sigterm_and_closes_cleanly(self):
        env = os.environ.copy()
        env.update(
            DATABASE_URL=self.database_url(),
            FLYMAIL_SESSION_SECRET="worker-signal-session-secret",
            FLYMAIL_DATA_DIR="/tmp/flymail-v2-worker-signal-test",
        )
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "worker.py",
            cwd=Path(__file__).resolve().parents[2],
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.sleep(1)
        process.send_signal(signal.SIGTERM)
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=10)

        self.assertEqual(process.returncode, 0, stderr.decode("utf-8", errors="replace"))
        self.assertNotIn(self.database_url(), stdout.decode("utf-8", errors="replace"))
        self.assertNotIn(self.database_url(), stderr.decode("utf-8", errors="replace"))

    async def test_worker_startup_runs_migrations_and_releases_expired_leases(self):
        job_id = await self._enqueue(
            job_kind="sync.reconcile",
            dedupe_key="startup-release",
            available_at=1,
        )
        async with self.worker_pool.acquire() as connection:
            jobs = JobRepository(connection)
            await connection.begin()
            await jobs.claim("sync", "worker-dead", 1, 1, now=1)
            await connection.commit()

        stop_event = asyncio.Event()
        stop_event.set()
        env = {
            "DATABASE_URL": self.database_url(),
            "FLYMAIL_SESSION_SECRET": "worker-startup-session-secret",
            "FLYMAIL_DATA_DIR": "/tmp/flymail-v2-worker-test",
        }
        dispatcher = WorkerDispatcher()

        async def reconcile_handler(_context, _payload):
            return JobOutcome.success()

        dispatcher.register("sync.reconcile", reconcile_handler)
        with patch.dict(os.environ, env, clear=True):
            await run_worker(
                stop_event=stop_event,
                now_fn=lambda: 10,
                dispatcher=dispatcher,
            )

        self.assertEqual(
            await self.scalar("SELECT status FROM worker_jobs WHERE id = %s", (job_id,)),
            "retry_wait",
        )


if __name__ == "__main__":
    unittest.main()
