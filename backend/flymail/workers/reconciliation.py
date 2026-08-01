"""Adaptive reconciliation planning and bounded mailbox orchestration."""

from __future__ import annotations

import hashlib
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

import aiomysql

from flymail.infrastructure.db.pool import DatabasePool
from flymail.infrastructure.db.uow import SqlUnitOfWork
from flymail.repositories.base import TenantContext
from flymail.repositories.jobs import JobRepository, JobSpec
from flymail.repositories.outbox import OutboxRepository


SYNC_INCREMENTAL = "sync.incremental"
SYNC_RECONCILE = "sync.reconcile"
SYNC_INITIAL = "sync.initial"
SYNC_MAILBOX_REFRESH = "sync.mailbox_refresh"
RECONCILIATION_PHASES = (
    "capability_cursor",
    "summary_changes",
    "deletions_memberships",
    "flags_labels",
    "cursor_update",
    "body_enqueue",
)


def _required_text(value: str, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{label} is required")
    return normalized


def _finite_non_negative(value: float, label: str) -> float:
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < 0:
        raise ValueError(f"{label} must be finite and non-negative")
    return normalized


@dataclass(frozen=True, slots=True)
class AccountReconciliationState:
    account_id: str
    provider_key: str
    last_user_view_at: float = 0
    recent_change_count: int = 0
    pending_operation_count: int = 0
    consecutive_failures: int = 0
    provider_min_interval_seconds: int = 60
    cooldown_until: float = 0
    auth_required: bool = False
    network_recovered: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "account_id", _required_text(self.account_id, "account_id"))
        object.__setattr__(
            self,
            "provider_key",
            _required_text(self.provider_key, "provider_key").casefold(),
        )
        object.__setattr__(
            self,
            "last_user_view_at",
            _finite_non_negative(self.last_user_view_at, "last_user_view_at"),
        )
        object.__setattr__(
            self,
            "cooldown_until",
            _finite_non_negative(self.cooldown_until, "cooldown_until"),
        )
        for field_name in (
            "recent_change_count",
            "pending_operation_count",
            "consecutive_failures",
        ):
            raw = getattr(self, field_name)
            if isinstance(raw, bool) or int(raw) < 0:
                raise ValueError(f"{field_name} must be non-negative")
            object.__setattr__(self, field_name, int(raw))
        minimum = self.provider_min_interval_seconds
        if isinstance(minimum, bool) or int(minimum) < 60:
            raise ValueError("provider_min_interval_seconds must be at least 60")
        object.__setattr__(self, "provider_min_interval_seconds", int(minimum))
        if not isinstance(self.auth_required, bool) or not isinstance(self.network_recovered, bool):
            raise TypeError("reconciliation flags must be bool")


@dataclass(frozen=True, slots=True)
class ReconciliationPlan:
    account_id: str
    status: str
    interval_seconds: int
    next_reconcile_at: float
    immediate: bool
    reason_code: str
    phases: tuple[str, ...] = RECONCILIATION_PHASES

    def __post_init__(self) -> None:
        object.__setattr__(self, "account_id", _required_text(self.account_id, "account_id"))
        status = str(self.status or "").strip().casefold()
        if status not in {"active", "normal", "quiet", "degraded", "auth_required"}:
            raise ValueError("unsupported reconciliation status")
        if isinstance(self.interval_seconds, bool) or int(self.interval_seconds) < 0:
            raise ValueError("interval_seconds must be non-negative")
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "interval_seconds", int(self.interval_seconds))
        object.__setattr__(
            self,
            "next_reconcile_at",
            _finite_non_negative(self.next_reconcile_at, "next_reconcile_at"),
        )
        if not isinstance(self.immediate, bool):
            raise TypeError("immediate must be bool")
        object.__setattr__(self, "reason_code", _required_text(self.reason_code, "reason_code"))
        if tuple(self.phases) != RECONCILIATION_PHASES:
            raise ValueError("reconciliation phases are fixed")


class ReconciliationPlanner:
    ACTIVE_SECONDS = 5 * 60
    NORMAL_SECONDS = 15 * 60
    QUIET_SECONDS = 30 * 60
    ACTIVE_VIEW_WINDOW_SECONDS = 10 * 60
    NORMAL_VIEW_WINDOW_SECONDS = 24 * 60 * 60
    MAX_BACKOFF_SECONDS = 60 * 60

    def __init__(
        self,
        *,
        jitter_fn: Callable[[str, int], int] | None = None,
    ) -> None:
        self._jitter_fn = jitter_fn or self._default_jitter

    @staticmethod
    def _default_jitter(account_id: str, failures: int) -> int:
        digest = hashlib.sha256(f"{account_id}:{failures}".encode("utf-8")).digest()
        return int.from_bytes(digest[:2], "big") % 31

    def plan(
        self,
        state: AccountReconciliationState,
        *,
        now: float | None = None,
    ) -> ReconciliationPlan:
        if not isinstance(state, AccountReconciliationState):
            raise TypeError("state must be AccountReconciliationState")
        timestamp = _finite_non_negative(time.time() if now is None else now, "now")
        if state.auth_required:
            return ReconciliationPlan(
                account_id=state.account_id,
                status="auth_required",
                interval_seconds=0,
                next_reconcile_at=max(timestamp + 86400, state.cooldown_until),
                immediate=False,
                reason_code="authorization_required",
            )

        if state.consecutive_failures:
            exponent = min(state.consecutive_failures - 1, 30)
            base = min(self.MAX_BACKOFF_SECONDS, 60 * (2**exponent))
            jitter = int(self._jitter_fn(state.account_id, state.consecutive_failures))
            if jitter < 0:
                raise ValueError("reconciliation jitter must be non-negative")
            interval = max(base + jitter, state.provider_min_interval_seconds)
            candidate = timestamp + interval
            next_at = max(candidate, state.cooldown_until)
            return ReconciliationPlan(
                account_id=state.account_id,
                status="degraded",
                interval_seconds=interval,
                next_reconcile_at=next_at,
                immediate=False,
                reason_code=(
                    "provider_cooldown"
                    if state.cooldown_until > candidate
                    else "failure_backoff"
                ),
            )

        recently_active = (
            state.last_user_view_at > 0
            and state.last_user_view_at >= timestamp - self.ACTIVE_VIEW_WINDOW_SECONDS
        )
        recently_viewed = (
            state.last_user_view_at > 0
            and state.last_user_view_at >= timestamp - self.NORMAL_VIEW_WINDOW_SECONDS
        )
        if (
            state.pending_operation_count > 0
            or state.recent_change_count >= 3
            or recently_active
        ):
            status = "active"
            base_interval = self.ACTIVE_SECONDS
        elif state.recent_change_count > 0 or recently_viewed:
            status = "normal"
            base_interval = self.NORMAL_SECONDS
        else:
            status = "quiet"
            base_interval = self.QUIET_SECONDS
        interval = max(base_interval, state.provider_min_interval_seconds)
        if state.network_recovered and state.cooldown_until <= timestamp:
            return ReconciliationPlan(
                account_id=state.account_id,
                status=status,
                interval_seconds=interval,
                next_reconcile_at=timestamp,
                immediate=True,
                reason_code="network_recovered",
            )
        candidate = timestamp + interval
        next_at = max(candidate, state.cooldown_until)
        return ReconciliationPlan(
            account_id=state.account_id,
            status=status,
            interval_seconds=interval,
            next_reconcile_at=next_at,
            immediate=False,
            reason_code=(
                "provider_cooldown"
                if state.cooldown_until > candidate
                else f"{status}_cadence"
            ),
        )


@dataclass(frozen=True, slots=True)
class MailboxReconciliationContext:
    user_uid: str
    account_id: str
    provider_key: str
    mailbox_id: str

    def __post_init__(self) -> None:
        for field_name in ("user_uid", "account_id", "mailbox_id"):
            object.__setattr__(
                self,
                field_name,
                _required_text(getattr(self, field_name), field_name),
            )
        object.__setattr__(
            self,
            "provider_key",
            _required_text(self.provider_key, "provider_key").casefold(),
        )


@dataclass(frozen=True, slots=True)
class SummaryChangeBatch:
    next_cursor: str
    message_ids: tuple[str, ...] = ()
    has_more: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "next_cursor", _required_text(self.next_cursor, "next_cursor"))
        object.__setattr__(
            self,
            "message_ids",
            tuple(
                dict.fromkeys(
                    _required_text(message_id, "message_id")
                    for message_id in self.message_ids
                )
            ),
        )
        if not isinstance(self.has_more, bool):
            raise TypeError("has_more must be bool")


@dataclass(frozen=True, slots=True)
class MailboxReconciliationResult:
    processed_messages: int
    next_cursor: str
    has_more: bool
    phases: tuple[str, ...] = RECONCILIATION_PHASES


class MailboxReconciliationBackend(Protocol):
    async def check_capabilities_and_cursor(
        self, context: MailboxReconciliationContext
    ) -> str: ...

    async def fetch_summary_changes(
        self,
        context: MailboxReconciliationContext,
        cursor: str,
        limit: int,
    ) -> SummaryChangeBatch: ...

    async def compare_remote_deletions(
        self,
        context: MailboxReconciliationContext,
        batch: SummaryChangeBatch,
    ) -> None: ...

    async def reconcile_flags_labels(
        self,
        context: MailboxReconciliationContext,
        batch: SummaryChangeBatch,
    ) -> None: ...

    async def persist_cursor(
        self,
        context: MailboxReconciliationContext,
        cursor: str,
    ) -> None: ...


class ReconciliationPublisher(Protocol):
    async def publish_body_work(
        self,
        context: MailboxReconciliationContext,
        message_ids: Sequence[str],
    ) -> None: ...

    async def publish_initial_continuation(
        self,
        context: MailboxReconciliationContext,
        cursor: str,
    ) -> None: ...


class ReconciliationRunner:
    def __init__(
        self,
        backend: MailboxReconciliationBackend,
        publisher: ReconciliationPublisher,
        *,
        batch_limit: int = 500,
    ) -> None:
        if isinstance(batch_limit, bool) or int(batch_limit) < 1:
            raise ValueError("batch_limit must be positive")
        self.backend = backend
        self.publisher = publisher
        self.batch_limit = int(batch_limit)

    async def run_mailbox(
        self,
        context: MailboxReconciliationContext,
        *,
        history: bool = False,
    ) -> MailboxReconciliationResult:
        cursor = await self.backend.check_capabilities_and_cursor(context)
        batch = await self.backend.fetch_summary_changes(
            context,
            cursor,
            self.batch_limit,
        )
        if not isinstance(batch, SummaryChangeBatch):
            raise TypeError("backend must return SummaryChangeBatch")
        if len(batch.message_ids) > self.batch_limit:
            raise ValueError("summary batch exceeds reconciliation limit")
        await self.backend.compare_remote_deletions(context, batch)
        await self.backend.reconcile_flags_labels(context, batch)
        await self.backend.persist_cursor(context, batch.next_cursor)
        if batch.message_ids:
            await self.publisher.publish_body_work(context, batch.message_ids)
        if history and batch.has_more:
            await self.publisher.publish_initial_continuation(
                context,
                batch.next_cursor,
            )
        return MailboxReconciliationResult(
            processed_messages=len(batch.message_ids),
            next_cursor=batch.next_cursor,
            has_more=batch.has_more,
        )


class SyncJobPublisher:
    """Publish secret-safe sync jobs and exactly one Outbox event per job."""

    def __init__(self, pool: DatabasePool) -> None:
        if not isinstance(pool, DatabasePool):
            raise TypeError("pool must be DatabasePool")
        self.pool = pool

    async def publish_incremental(
        self,
        account,
        *,
        reason: str,
        now: float | None = None,
    ) -> str:
        return await self._publish(
            account=account,
            queue_name="realtime",
            job_kind=SYNC_INCREMENTAL,
            dedupe_key=f"sync.incremental:{account.account_id}:{account.mailbox_id}",
            payload={
                "account_id": account.account_id,
                "mailbox_id": account.mailbox_id,
                "reason": _required_text(reason, "reason"),
            },
            event_type="sync.incremental.requested",
            now=now,
        )

    async def publish_reconcile(
        self,
        account,
        *,
        reason: str,
        now: float | None = None,
    ) -> str:
        return await self._publish(
            account=account,
            queue_name="reconcile",
            job_kind=SYNC_RECONCILE,
            dedupe_key=f"sync.reconcile:{account.account_id}",
            payload={
                "account_id": account.account_id,
                "reason": _required_text(reason, "reason"),
            },
            event_type="sync.reconcile.requested",
            now=now,
        )

    async def publish_mailbox_refresh(
        self,
        account,
        *,
        reason: str,
        now: float | None = None,
    ) -> str:
        return await self._publish(
            account=account,
            queue_name="realtime",
            job_kind=SYNC_MAILBOX_REFRESH,
            dedupe_key=f"sync.mailbox_refresh:{account.account_id}:{account.mailbox_id}",
            payload={
                "account_id": account.account_id,
                "mailbox_id": account.mailbox_id,
                "reason": _required_text(reason, "reason"),
            },
            event_type="sync.mailbox_refresh.requested",
            now=now,
        )

    async def publish_body_work(
        self,
        context: MailboxReconciliationContext,
        message_ids: Sequence[str],
    ) -> None:
        for message_id in dict.fromkeys(message_ids):
            await self._publish_context_job(
                context,
                queue_name="history",
                job_kind="content.body",
                dedupe_key=f"content.body:{message_id}",
                payload={
                    "account_id": context.account_id,
                    "mailbox_id": context.mailbox_id,
                    "message_id": _required_text(message_id, "message_id"),
                },
            )

    async def publish_initial_continuation(
        self,
        context: MailboxReconciliationContext,
        cursor: str,
    ) -> None:
        normalized_cursor = _required_text(cursor, "cursor")
        await self._publish_context_job(
            context,
            queue_name="history",
            job_kind=SYNC_INITIAL,
            dedupe_key=(
                f"sync.initial:{context.account_id}:{context.mailbox_id}:"
                f"{hashlib.sha256(normalized_cursor.encode('utf-8')).hexdigest()[:16]}"
            ),
            payload={
                "account_id": context.account_id,
                "mailbox_id": context.mailbox_id,
                "cursor": normalized_cursor,
            },
        )

    async def _publish_context_job(
        self,
        context: MailboxReconciliationContext,
        *,
        queue_name: str,
        job_kind: str,
        dedupe_key: str,
        payload: dict,
    ) -> str:
        class AccountScope:
            user_uid = context.user_uid
            account_id = context.account_id
            provider_key = context.provider_key
            mailbox_id = context.mailbox_id

        return await self._publish(
            account=AccountScope(),
            queue_name=queue_name,
            job_kind=job_kind,
            dedupe_key=dedupe_key,
            payload=payload,
            event_type=f"{job_kind}.requested",
            now=None,
        )

    async def _publish(
        self,
        *,
        account,
        queue_name: str,
        job_kind: str,
        dedupe_key: str,
        payload: dict,
        event_type: str,
        now: float | None,
    ) -> str:
        timestamp = _finite_non_negative(time.time() if now is None else now, "now")
        tenant = TenantContext(_required_text(account.user_uid, "user_uid"))
        account_id = _required_text(account.account_id, "account_id")
        provider_key = _required_text(account.provider_key, "provider_key").casefold()
        async with SqlUnitOfWork(self.pool) as uow:
            if uow.connection is None:
                raise RuntimeError("sync publisher unit of work has no connection")
            job_id = await JobRepository(uow.connection).enqueue(
                JobSpec(
                    queue_name=queue_name,
                    job_kind=job_kind,
                    user_uid=tenant.user_uid,
                    account_id=account_id,
                    provider_key=provider_key,
                    priority=10,
                    available_at=timestamp,
                    max_attempts=10,
                    dedupe_key=dedupe_key,
                    payload=payload,
                ),
                now=timestamp,
            )
            if not await self._outbox_exists(
                uow.connection,
                job_id,
                event_type,
            ):
                await OutboxRepository(
                    uow.connection,
                    tenant,
                ).append(
                    event_type,
                    job_id,
                    {
                        "job_id": job_id,
                        **payload,
                    },
                    aggregate_type="sync_job",
                    now=timestamp,
                )
            await uow.commit()
            return job_id

    @staticmethod
    async def _outbox_exists(
        connection: aiomysql.Connection,
        job_id: str,
        event_type: str,
    ) -> bool:
        async with connection.cursor() as cursor:
            await cursor.execute(
                """
                SELECT id
                FROM outbox_events
                WHERE aggregate_type = 'sync_job'
                  AND aggregate_id = %s
                  AND event_type = %s
                LIMIT 1
                FOR UPDATE
                """,
                (job_id, event_type),
            )
            return await cursor.fetchone() is not None
