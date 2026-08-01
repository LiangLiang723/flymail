from __future__ import annotations

import asyncio
import time
import unittest
from collections.abc import AsyncIterator

from flymail.infrastructure.db.migrations.runner import run_migrations
from flymail.providers.core.imap_commands import IdleEvent
from flymail.providers.core.rate_limit import AccountConnectionLimiter
from flymail.providers.errors import ProviderError, ProviderErrorCode
from flymail.providers.registry import ProviderRegistry
from flymail.repositories.accounts import AccountRepository
from flymail.repositories.base import AdminContext, TenantContext
from flymail.repositories.users import UserRepository
from flymail.workers.idle import IdleAccountSnapshot, IdleSupervisor
from flymail.workers.reconciliation import (
    AccountReconciliationState,
    MailboxReconciliationContext,
    ReconciliationPlanner,
    ReconciliationRunner,
    SummaryChangeBatch,
    SyncJobPublisher,
)
from tests.v2.mysql_test_case import MySqlIsolatedAsyncioTestCase


class ReconciliationPlannerTests(unittest.TestCase):
    def planner(self, jitter: int = 0) -> ReconciliationPlanner:
        return ReconciliationPlanner(jitter_fn=lambda _account, _failures: jitter)

    def state(self, **overrides) -> AccountReconciliationState:
        values = {
            "account_id": "acc-plan",
            "provider_key": "generic",
            "last_user_view_at": 0,
            "recent_change_count": 0,
            "pending_operation_count": 0,
            "consecutive_failures": 0,
            "provider_min_interval_seconds": 60,
            "cooldown_until": 0,
            "auth_required": False,
            "network_recovered": False,
        }
        values.update(overrides)
        return AccountReconciliationState(**values)

    def test_active_normal_and_quiet_intervals_are_exact(self):
        now = 100_000
        active = self.planner().plan(
            self.state(last_user_view_at=now - 60), now=now
        )
        normal = self.planner().plan(
            self.state(last_user_view_at=now - 3600), now=now
        )
        quiet = self.planner().plan(self.state(), now=now)
        self.assertEqual((active.status, active.interval_seconds), ("active", 300))
        self.assertEqual((normal.status, normal.interval_seconds), ("normal", 900))
        self.assertEqual((quiet.status, quiet.interval_seconds), ("quiet", 1800))
        self.assertEqual(active.next_reconcile_at, now + 300)
        self.assertEqual(normal.next_reconcile_at, now + 900)
        self.assertEqual(quiet.next_reconcile_at, now + 1800)

    def test_never_viewed_account_is_not_active_when_clock_is_near_epoch(self):
        plan = self.planner().plan(self.state(last_user_view_at=0), now=100)
        self.assertEqual(plan.status, "quiet")

    def test_activity_inputs_and_provider_minimum_promote_active_state(self):
        now = 200_000
        for state in (
            self.state(pending_operation_count=1),
            self.state(recent_change_count=3),
        ):
            plan = self.planner().plan(state, now=now)
            self.assertEqual(plan.status, "active")
            self.assertEqual(plan.interval_seconds, 300)
        limited = self.planner().plan(
            self.state(pending_operation_count=1, provider_min_interval_seconds=1200),
            now=now,
        )
        self.assertEqual(limited.interval_seconds, 1200)

    def test_failures_back_off_exponentially_with_jitter_and_cooldown(self):
        now = 300_000
        degraded = self.planner(jitter=7).plan(
            self.state(consecutive_failures=3), now=now
        )
        self.assertEqual(degraded.status, "degraded")
        self.assertEqual(degraded.interval_seconds, 247)
        self.assertEqual(degraded.next_reconcile_at, now + 247)
        cooled = self.planner(jitter=7).plan(
            self.state(consecutive_failures=3, cooldown_until=now + 600), now=now
        )
        self.assertEqual(cooled.next_reconcile_at, now + 600)
        self.assertEqual(cooled.reason_code, "provider_cooldown")

    def test_network_recovery_is_immediate_without_delaying_other_account(self):
        now = 400_000
        recovered = self.planner().plan(
            self.state(account_id="acc-a", network_recovered=True), now=now
        )
        failed = self.planner(jitter=5).plan(
            self.state(account_id="acc-b", consecutive_failures=2), now=now
        )
        self.assertTrue(recovered.immediate)
        self.assertEqual(recovered.next_reconcile_at, now)
        self.assertFalse(failed.immediate)
        self.assertEqual(failed.next_reconcile_at, now + 125)

    def test_auth_required_pauses_normal_cadence(self):
        now = 500_000
        plan = self.planner().plan(self.state(auth_required=True), now=now)
        self.assertEqual(plan.status, "auth_required")
        self.assertEqual(plan.interval_seconds, 0)
        self.assertFalse(plan.immediate)
        self.assertGreaterEqual(plan.next_reconcile_at, now + 86400)


class FakeAccountSource:
    def __init__(self, snapshot: IdleAccountSnapshot) -> None:
        self.snapshot = snapshot
        self.current = True
        self.loads = 0

    async def load(self, account_id: str) -> IdleAccountSnapshot | None:
        self.loads += 1
        return self.snapshot if account_id == self.snapshot.account_id else None

    async def is_current(self, snapshot: IdleAccountSnapshot) -> bool:
        return self.current and snapshot == self.snapshot


class FakeIdleSession:
    def __init__(
        self,
        events: tuple[IdleEvent, ...] = (),
        *,
        stop_event: asyncio.Event | None = None,
        block: bool = False,
    ) -> None:
        self.events = events
        self.stop_event = stop_event
        self.block = block
        self.disconnected = False
        self.idle_calls: list[str] = []

    def idle(self, mailbox_native_key: str) -> AsyncIterator[IdleEvent]:
        self.idle_calls.append(mailbox_native_key)

        async def generate() -> AsyncIterator[IdleEvent]:
            for event in self.events:
                yield event
            if self.stop_event is not None:
                self.stop_event.set()
            if self.block:
                await asyncio.Event().wait()
            return
            yield IdleEvent("timeout")

        return generate()

    async def disconnect(self) -> None:
        self.disconnected = True


class FakeSessionFactory:
    def __init__(self, results, *, stop_on_open: int | None = None, stop_event=None) -> None:
        self.results = list(results)
        self.stop_on_open = stop_on_open
        self.stop_event = stop_event
        self.opens = 0

    async def open(self, _snapshot: IdleAccountSnapshot):
        self.opens += 1
        if self.stop_on_open == self.opens and self.stop_event is not None:
            self.stop_event.set()
        result = self.results[min(self.opens - 1, len(self.results) - 1)]
        if isinstance(result, BaseException):
            raise result
        return result


class FakePublisher:
    def __init__(self, stop_event: asyncio.Event | None = None) -> None:
        self.stop_event = stop_event
        self.incremental: list[str] = []
        self.reconcile: list[str] = []
        self.refresh: list[str] = []
        self.body_batches: list[tuple[str, ...]] = []
        self.initial: list[str] = []

    async def publish_incremental(self, account, *, reason: str, now: float | None = None):
        del account, now
        self.incremental.append(reason)
        return "job-incremental"

    async def publish_reconcile(self, account, *, reason: str, now: float | None = None):
        del account, now
        self.reconcile.append(reason)
        return "job-reconcile"

    async def publish_mailbox_refresh(self, account, *, reason: str, now: float | None = None):
        del account, now
        self.refresh.append(reason)
        if self.stop_event is not None:
            self.stop_event.set()
        return "job-refresh"

    async def publish_body_work(self, context, message_ids):
        del context
        self.body_batches.append(tuple(message_ids))

    async def publish_initial_continuation(self, context, cursor: str):
        del context
        self.initial.append(cursor)


class IdleSupervisorTests(unittest.IsolatedAsyncioTestCase):
    def snapshot(self, **overrides) -> IdleAccountSnapshot:
        values = {
            "account_id": "acc-idle",
            "user_uid": "usr-idle",
            "provider_key": "generic",
            "mailbox_id": "mbx-inbox",
            "mailbox_native_key": "INBOX",
            "credential_version": 1,
            "status": "active",
            "supports_idle": True,
            "idle_refresh_seconds": 0.02,
            "poll_seconds": 0.01,
        }
        values.update(overrides)
        return IdleAccountSnapshot(**values)

    def limiter(self) -> AccountConnectionLimiter:
        return AccountConnectionLimiter(ProviderRegistry.default())

    async def test_blocking_idle_exits_after_account_disable_without_waiting_refresh(self):
        source = FakeAccountSource(self.snapshot(idle_refresh_seconds=30))
        session = FakeIdleSession(block=True)
        supervisor = IdleSupervisor(
            source,
            FakeSessionFactory((session,)),
            FakePublisher(),
            self.limiter(),
            state_check_seconds=0.01,
            reconnect_delay_seconds=0,
        )
        task = asyncio.create_task(supervisor.run_account("acc-idle"))
        await asyncio.sleep(0.02)
        source.current = False
        await asyncio.wait_for(task, timeout=1)
        self.assertTrue(session.disconnected)

    async def test_idle_refreshes_before_timeout_and_releases_session(self):
        stop = asyncio.Event()
        first = FakeIdleSession(block=True)
        second = FakeIdleSession(stop_event=stop)
        factory = FakeSessionFactory(
            (first, second), stop_on_open=2, stop_event=stop
        )
        supervisor = IdleSupervisor(
            FakeAccountSource(self.snapshot()),
            factory,
            FakePublisher(),
            self.limiter(),
            stop_event=stop,
            reconnect_delay_seconds=0,
        )
        await asyncio.wait_for(supervisor.run_account("acc-idle"), timeout=1)
        self.assertEqual(factory.opens, 2)
        self.assertTrue(first.disconnected)

    async def test_unsupported_idle_falls_back_to_mailbox_refresh(self):
        stop = asyncio.Event()
        publisher = FakePublisher(stop)
        supervisor = IdleSupervisor(
            FakeAccountSource(self.snapshot(supports_idle=False)),
            FakeSessionFactory((FakeIdleSession(),)),
            publisher,
            self.limiter(),
            stop_event=stop,
            reconnect_delay_seconds=0,
        )
        await asyncio.wait_for(supervisor.run_account("acc-idle"), timeout=1)
        self.assertEqual(publisher.refresh, ["idle_unsupported"])

    async def test_empty_idle_stream_waits_before_reconnect(self):
        stop = asyncio.Event()
        waits: list[float] = []

        async def sleep(seconds: float) -> None:
            waits.append(seconds)
            stop.set()

        supervisor = IdleSupervisor(
            FakeAccountSource(self.snapshot()),
            FakeSessionFactory((FakeIdleSession(),)),
            FakePublisher(),
            self.limiter(),
            stop_event=stop,
            reconnect_delay_seconds=5,
            sleep_fn=sleep,
        )
        await asyncio.wait_for(supervisor.run_account("acc-idle"), timeout=1)
        self.assertEqual(waits, [5])

    async def test_auth_error_stops_without_network_recovery_retry(self):
        publisher = FakePublisher()
        factory = FakeSessionFactory(
            (ProviderError(ProviderErrorCode.AUTHORIZATION_REQUIRED),)
        )
        supervisor = IdleSupervisor(
            FakeAccountSource(self.snapshot()),
            factory,
            publisher,
            self.limiter(),
            reconnect_delay_seconds=0,
        )
        await asyncio.wait_for(supervisor.run_account("acc-idle"), timeout=1)
        self.assertEqual(factory.opens, 1)
        self.assertEqual(publisher.reconcile, [])

    async def test_network_recovery_enqueues_immediate_reconcile(self):
        stop = asyncio.Event()
        session = FakeIdleSession(stop_event=stop)
        factory = FakeSessionFactory((ConnectionError("offline"), session))
        publisher = FakePublisher()
        supervisor = IdleSupervisor(
            FakeAccountSource(self.snapshot()),
            factory,
            publisher,
            self.limiter(),
            stop_event=stop,
            reconnect_delay_seconds=0,
        )
        await asyncio.wait_for(supervisor.run_account("acc-idle"), timeout=1)
        self.assertEqual(publisher.reconcile, ["network_recovered"])
        self.assertEqual(factory.opens, 2)

    async def test_credential_change_or_disable_stops_supervisor(self):
        stop = asyncio.Event()
        source = FakeAccountSource(self.snapshot())

        class ChangingSession(FakeIdleSession):
            def idle(self, mailbox_native_key: str):
                async def generate():
                    source.current = False
                    yield IdleEvent("exists", count=1)
                return generate()

        supervisor = IdleSupervisor(
            source,
            FakeSessionFactory((ChangingSession(),)),
            FakePublisher(),
            self.limiter(),
            stop_event=stop,
            reconnect_delay_seconds=0,
        )
        await asyncio.wait_for(supervisor.run_account("acc-idle"), timeout=1)
        self.assertFalse(stop.is_set())


class ReconciliationRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_backend_cannot_return_more_than_batch_limit(self):
        class Backend:
            async def check_capabilities_and_cursor(self, context):
                return "cursor-1"

            async def fetch_summary_changes(self, context, cursor, limit):
                return SummaryChangeBatch(
                    next_cursor="cursor-2",
                    message_ids=tuple(f"msg-{index}" for index in range(limit + 1)),
                )

            async def compare_remote_deletions(self, context, batch):
                raise AssertionError("over-limit batch must stop before reconciliation")

            async def reconcile_flags_labels(self, context, batch):
                raise AssertionError("over-limit batch must stop before reconciliation")

            async def persist_cursor(self, context, cursor):
                raise AssertionError("over-limit batch must not persist cursor")

        runner = ReconciliationRunner(Backend(), FakePublisher(), batch_limit=3)
        context = MailboxReconciliationContext(
            user_uid="usr-runner",
            account_id="acc-runner",
            provider_key="generic",
            mailbox_id="mbx-runner",
        )
        with self.assertRaises(ValueError):
            await runner.run_mailbox(context)

    async def test_mailbox_phases_are_bounded_and_body_work_is_separate(self):
        calls: list[object] = []

        class Backend:
            async def check_capabilities_and_cursor(self, context):
                calls.append("capability_cursor")
                return "cursor-1"

            async def fetch_summary_changes(self, context, cursor, limit):
                calls.append(("summary", cursor, limit))
                return SummaryChangeBatch(
                    next_cursor="cursor-2",
                    message_ids=("msg-1", "msg-2"),
                    has_more=True,
                )

            async def compare_remote_deletions(self, context, batch):
                calls.append("deletions_memberships")

            async def reconcile_flags_labels(self, context, batch):
                calls.append("flags_labels")

            async def persist_cursor(self, context, cursor):
                calls.append(("persist_cursor", cursor))

        publisher = FakePublisher()
        runner = ReconciliationRunner(Backend(), publisher, batch_limit=50)
        context = MailboxReconciliationContext(
            user_uid="usr-runner",
            account_id="acc-runner",
            provider_key="generic",
            mailbox_id="mbx-runner",
        )
        result = await runner.run_mailbox(context, history=True)
        self.assertEqual(
            calls,
            [
                "capability_cursor",
                ("summary", "cursor-1", 50),
                "deletions_memberships",
                "flags_labels",
                ("persist_cursor", "cursor-2"),
            ],
        )
        self.assertEqual(publisher.body_batches, [("msg-1", "msg-2")])
        self.assertEqual(publisher.initial, ["cursor-2"])
        self.assertTrue(result.has_more)
        self.assertEqual(result.processed_messages, 2)


class IdlePublishingIntegrationTests(MySqlIsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await run_migrations(self.pool)
        await self._clear_tables()
        self.tenant, self.account = await self._create_scope()

    async def _clear_tables(self) -> None:
        tables = (
            "job_attempts",
            "worker_jobs",
            "outbox_events",
            "messages",
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

    async def _create_scope(self):
        async with self.pool.acquire() as connection:
            await connection.begin()
            user = await UserRepository(connection).create_user_for_admin(
                AdminContext("usr-idle-admin"),
                username="idle-user",
                password_hash="test-password-hash",
            )
            tenant = TenantContext(user.id)
            account = await AccountRepository(connection).create_account(
                tenant,
                provider_key="generic",
                email="idle@example.com",
                status="active",
            )
            await connection.commit()
        return tenant, account

    async def test_concurrent_incremental_publication_has_one_job_and_one_outbox(self):
        snapshot = IdleAccountSnapshot(
            account_id=self.account.id,
            user_uid=self.tenant.user_uid,
            provider_key="generic",
            mailbox_id="mbx-inbox",
            mailbox_native_key="INBOX",
            credential_version=1,
            status="active",
            supports_idle=True,
            idle_refresh_seconds=30,
            poll_seconds=60,
        )
        publisher = SyncJobPublisher(self.api_pool)
        job_ids = await asyncio.gather(
            *(
                publisher.publish_incremental(
                    snapshot,
                    reason="message_exists",
                    now=100,
                )
                for _ in range(8)
            )
        )
        self.assertEqual(len(set(job_ids)), 1)
        self.assertEqual(
            int(await self.scalar(
                "SELECT COUNT(*) FROM worker_jobs WHERE job_kind = 'sync.incremental'"
            ) or 0),
            1,
        )
        self.assertEqual(
            int(await self.scalar(
                "SELECT COUNT(*) FROM outbox_events WHERE event_type = 'sync.incremental.requested'"
            ) or 0),
            1,
        )

    async def test_history_continuation_is_scoped_by_cursor(self):
        context = MailboxReconciliationContext(
            user_uid=self.tenant.user_uid,
            account_id=self.account.id,
            provider_key="generic",
            mailbox_id="mbx-inbox",
        )
        publisher = SyncJobPublisher(self.api_pool)
        await publisher.publish_initial_continuation(context, "cursor-1")
        await publisher.publish_initial_continuation(context, "cursor-1")
        await publisher.publish_initial_continuation(context, "cursor-2")
        self.assertEqual(
            int(await self.scalar(
                "SELECT COUNT(*) FROM worker_jobs WHERE job_kind = 'sync.initial'"
            ) or 0),
            2,
        )

    async def test_exists_expunge_and_flags_enqueue_one_deduped_job_without_message_write(self):
        stop = asyncio.Event()
        snapshot = IdleAccountSnapshot(
            account_id=self.account.id,
            user_uid=self.tenant.user_uid,
            provider_key="generic",
            mailbox_id="mbx-inbox",
            mailbox_native_key="INBOX",
            credential_version=1,
            status="active",
            supports_idle=True,
            idle_refresh_seconds=30,
            poll_seconds=60,
        )
        session = FakeIdleSession(
            (
                IdleEvent("exists", count=3),
                IdleEvent("expunge", sequence=2),
                IdleEvent("fetch", sequence=1),
            ),
            stop_event=stop,
        )
        supervisor = IdleSupervisor(
            FakeAccountSource(snapshot),
            FakeSessionFactory((session,)),
            SyncJobPublisher(self.api_pool),
            AccountConnectionLimiter(ProviderRegistry.default()),
            stop_event=stop,
            reconnect_delay_seconds=0,
            now_fn=lambda: 100,
        )
        await supervisor.run_account(self.account.id)
        self.assertEqual(
            int(await self.scalar(
                "SELECT COUNT(*) FROM worker_jobs WHERE job_kind = 'sync.incremental'"
            ) or 0),
            1,
        )
        self.assertEqual(
            int(await self.scalar(
                "SELECT COUNT(*) FROM outbox_events WHERE event_type = 'sync.incremental.requested'"
            ) or 0),
            1,
        )
        self.assertEqual(int(await self.scalar("SELECT COUNT(*) FROM messages") or 0), 0)
        payload = await self.scalar(
            "SELECT payload FROM worker_jobs WHERE job_kind = 'sync.incremental'"
        )
        self.assertNotIn("body", str(payload).casefold())
        self.assertNotIn("mime", str(payload).casefold())


if __name__ == "__main__":
    unittest.main()
