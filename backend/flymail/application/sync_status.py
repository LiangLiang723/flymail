"""Tenant-scoped sync-center projections, conflict actions, and safe diagnostics."""

from __future__ import annotations

import json
import time
from typing import Any

import aiomysql

from flymail.api.schemas.sync import (
    AdminDiagnosticsResponse,
    ConflictItem,
    ConflictResolutionResponse,
    SyncAccountStatus,
    SyncCenterResponse,
    SyncPhaseStatus,
    SyncTaskResponse,
)
from flymail.application.auth import AuthenticatedSession
from flymail.application.realtime import RealtimeService
from flymail.domain.errors import ConflictError, NotFoundError
from flymail.infrastructure.db.pool import DatabasePool
from flymail.repositories.audit import AuditRepository
from flymail.repositories.base import TenantContext
from flymail.repositories.jobs import JobRepository, JobSpec
from flymail.repositories.runtime import RuntimeRepository


_PHASES = ("summary", "body", "index", "state")
_RUNNABLE_JOB_STATES = ("pending", "retry_wait", "leased", "running")


def _json_object(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    try:
        decoded = json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return {}
    return dict(decoded) if isinstance(decoded, dict) else {}


class SyncStatusService:
    def __init__(
        self,
        pool: DatabasePool,
        realtime: RealtimeService,
        *,
        now_fn=time.time,
    ) -> None:
        if not isinstance(pool, DatabasePool):
            raise TypeError("pool must be DatabasePool")
        if not isinstance(realtime, RealtimeService):
            raise TypeError("realtime must be RealtimeService")
        self.pool = pool
        self.realtime = realtime
        self.now_fn = now_fn

    async def center(self, session: AuthenticatedSession) -> SyncCenterResponse:
        tenant = TenantContext(session.user.id)
        async with self.pool.acquire() as connection:
            async with connection.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(
                    """
                    SELECT a.id AS account_id,
                           COALESCE(r.status, 'normal') AS runtime_status,
                           COALESCE(r.idle_status, 'disconnected') AS idle_status,
                           COALESCE(r.last_activity_at, 0) AS last_activity_at,
                           COALESCE(r.next_reconcile_at, 0) AS next_reconcile_at,
                           COALESCE(r.failure_count, 0) AS failure_count,
                           COALESCE(r.backoff_until, 0) AS backoff_until
                    FROM mail_accounts a
                    LEFT JOIN account_runtime_state r
                      ON r.account_id = a.id AND r.user_uid = a.user_uid
                    WHERE a.user_uid = %s AND a.status <> 'deleting'
                    ORDER BY a.created_at, a.id
                    LIMIT 500
                    """,
                    (tenant.user_uid,),
                )
                accounts = [dict(row) for row in await cursor.fetchall()]
                await cursor.execute(
                    """
                    SELECT account_id, phase, cursor_json, updated_at
                    FROM sync_cursors
                    WHERE user_uid = %s AND mailbox_id = ''
                      AND phase IN ('summary', 'body', 'index', 'state')
                    ORDER BY account_id, phase
                    """,
                    (tenant.user_uid,),
                )
                cursor_rows = [dict(row) for row in await cursor.fetchall()]
                await cursor.execute(
                    """
                    SELECT account_id,
                           SUM(status IN ('pending', 'applying', 'retry_wait')) AS pending_count,
                           SUM(status IN ('conflict', 'review_required')) AS conflict_count
                    FROM mail_operations
                    WHERE user_uid = %s
                    GROUP BY account_id
                    """,
                    (tenant.user_uid,),
                )
                operation_rows = [dict(row) for row in await cursor.fetchall()]

        phases_by_account: dict[str, dict[str, SyncPhaseStatus]] = {}
        for row in cursor_rows:
            progress = _json_object(row["cursor_json"])
            completed = max(int(progress.get("completed") or 0), 0)
            total = max(int(progress.get("total") or 0), completed)
            phases_by_account.setdefault(str(row["account_id"]), {})[
                str(row["phase"])
            ] = SyncPhaseStatus(
                completed=completed,
                total=total,
                updated_at=float(row["updated_at"] or 0),
            )
        counts = {
            str(row["account_id"] or ""): (
                int(row["pending_count"] or 0),
                int(row["conflict_count"] or 0),
            )
            for row in operation_rows
        }
        items: list[SyncAccountStatus] = []
        for row in accounts:
            account_id = str(row["account_id"])
            phases = phases_by_account.get(account_id, {})
            for phase in _PHASES:
                phases.setdefault(
                    phase,
                    SyncPhaseStatus(completed=0, total=0, updated_at=0),
                )
            pending, conflicts = counts.get(account_id, (0, 0))
            items.append(
                SyncAccountStatus(
                    account_id=account_id,
                    status=str(row["runtime_status"]),
                    idle_status=str(row["idle_status"]),
                    last_activity_at=float(row["last_activity_at"] or 0),
                    next_reconcile_at=float(row["next_reconcile_at"] or 0),
                    failure_count=max(int(row["failure_count"] or 0), 0),
                    backoff_until=float(row["backoff_until"] or 0),
                    phases=phases,
                    pending_operations=pending,
                    conflicts=conflicts,
                )
            )
        return SyncCenterResponse(accounts=tuple(items))

    async def request_refresh(
        self,
        session: AuthenticatedSession,
        account_id: str,
        *,
        request_id: str,
    ) -> SyncTaskResponse:
        tenant = TenantContext(session.user.id)
        normalized_account = str(account_id or "").strip()
        timestamp = float(self.now_fn())
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                async with connection.cursor(aiomysql.DictCursor) as cursor:
                    await cursor.execute(
                        """
                        SELECT id, provider_key
                        FROM mail_accounts
                        WHERE id = %s AND user_uid = %s AND status = 'active'
                        FOR UPDATE
                        """,
                        (normalized_account, tenant.user_uid),
                    )
                    account = await cursor.fetchone()
                if account is None:
                    raise NotFoundError("mail account was not found")
                task_id = await JobRepository(connection).enqueue(
                    JobSpec(
                        queue_name="reconcile",
                        job_kind="sync.reconcile",
                        payload={"account_id": normalized_account},
                        user_uid=tenant.user_uid,
                        account_id=normalized_account,
                        provider_key=str(account["provider_key"]),
                        priority=50,
                        available_at=timestamp,
                        max_attempts=20,
                        dedupe_key=f"manual-sync-refresh:{normalized_account}",
                    ),
                    now=timestamp,
                )
                await AuditRepository(connection).append(
                    event_type="sync.refresh_requested",
                    result_code="success",
                    request_id=request_id,
                    user_uid=tenant.user_uid,
                    actor_user_uid=tenant.user_uid,
                    resource_type="mail_account",
                    resource_id=normalized_account,
                    safe_metadata={"task_id": task_id},
                    now=timestamp,
                )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
        return SyncTaskResponse(task_id=task_id)

    async def list_conflicts(
        self,
        session: AuthenticatedSession,
    ) -> tuple[ConflictItem, ...]:
        tenant = TenantContext(session.user.id)
        async with self.pool.acquire() as connection:
            async with connection.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(
                    """
                    SELECT id, operation_type, target_type, target_id,
                           account_id, status, last_error_class,
                           last_error_message, created_at, updated_at
                    FROM mail_operations
                    WHERE user_uid = %s
                      AND status IN ('conflict', 'review_required')
                    ORDER BY updated_at DESC, id DESC
                    LIMIT 200
                    """,
                    (tenant.user_uid,),
                )
                rows = [dict(row) for row in await cursor.fetchall()]
        return tuple(
            ConflictItem(
                operation_id=str(row["id"]),
                operation_type=str(row["operation_type"]),
                target_type=str(row["target_type"]),
                target_id=str(row["target_id"]),
                account_id=str(row["account_id"]) if row["account_id"] else None,
                status=str(row["status"]),
                error_class=str(row["last_error_class"] or ""),
                error_message=str(row["last_error_message"] or ""),
                created_at=float(row["created_at"] or 0),
                updated_at=float(row["updated_at"] or 0),
            )
            for row in rows
        )

    async def resolve_conflict(
        self,
        session: AuthenticatedSession,
        operation_id: str,
        *,
        action: str,
        mailbox_id: str | None,
        request_id: str,
    ) -> ConflictResolutionResponse:
        tenant = TenantContext(session.user.id)
        normalized_id = str(operation_id or "").strip()
        timestamp = float(self.now_fn())
        task_id: str | None = None
        next_status = "cancelled" if action == "cancel_operation" else "pending"
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                async with connection.cursor(aiomysql.DictCursor) as cursor:
                    await cursor.execute(
                        """
                        SELECT o.id, o.account_id, o.desired_state, o.status,
                               a.provider_key
                        FROM mail_operations o
                        LEFT JOIN mail_accounts a
                          ON a.id = o.account_id AND a.user_uid = o.user_uid
                        WHERE o.id = %s AND o.user_uid = %s
                          AND o.status IN ('conflict', 'review_required')
                        FOR UPDATE
                        """,
                        (normalized_id, tenant.user_uid),
                    )
                    row = await cursor.fetchone()
                    if row is None:
                        raise NotFoundError("operation conflict was not found")
                    desired = _json_object(row["desired_state"])
                    if mailbox_id is not None:
                        desired["mailbox_id"] = str(mailbox_id).strip()
                    if action == "cancel_operation":
                        await cursor.execute(
                            """
                            UPDATE mail_operations
                            SET status = 'cancelled', completed_at = %s,
                                last_error_class = 'UserCancelled',
                                last_error_message = '', updated_at = %s
                            WHERE id = %s AND user_uid = %s
                            """,
                            (timestamp, timestamp, normalized_id, tenant.user_uid),
                        )
                    elif action == "retry_operation":
                        account_id = str(row["account_id"] or "")
                        provider_key = str(row["provider_key"] or "")
                        if not account_id or not provider_key:
                            raise ConflictError("operation account is unavailable")
                        await cursor.execute(
                            """
                            UPDATE mail_operations
                            SET desired_state = %s, status = 'pending',
                                available_at = %s, last_error_class = '',
                                last_error_message = '', completed_at = NULL,
                                updated_at = %s
                            WHERE id = %s AND user_uid = %s
                            """,
                            (
                                json.dumps(desired, ensure_ascii=False, sort_keys=True),
                                timestamp,
                                timestamp,
                                normalized_id,
                                tenant.user_uid,
                            ),
                        )
                        task_id = await JobRepository(connection).enqueue(
                            JobSpec(
                                queue_name="operations",
                                job_kind="mail.operation.apply",
                                payload={"operation_id": normalized_id},
                                user_uid=tenant.user_uid,
                                account_id=account_id,
                                provider_key=provider_key,
                                priority=20,
                                available_at=timestamp,
                                max_attempts=20,
                                dedupe_key=f"mail-operation:{normalized_id}",
                            ),
                            now=timestamp,
                        )
                    else:
                        raise ValueError("unsupported conflict resolution action")
                await AuditRepository(connection).append(
                    event_type=f"sync.conflict.{action}",
                    result_code="success",
                    request_id=request_id,
                    user_uid=tenant.user_uid,
                    actor_user_uid=tenant.user_uid,
                    resource_type="mail_operation",
                    resource_id=normalized_id,
                    safe_metadata={"status": next_status},
                    now=timestamp,
                )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
        await self.realtime.publish(
            tenant,
            event_type="conflict.created" if next_status == "pending" else "operation.updated",
            aggregate_type="mail_operation",
            aggregate_id=normalized_id,
            payload={"operation_id": normalized_id, "status": next_status},
        )
        return ConflictResolutionResponse(
            operation_id=normalized_id,
            status=next_status,
            task_id=task_id,
        )

    async def diagnostics(self) -> AdminDiagnosticsResponse:
        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("SELECT COUNT(*) FROM users")
                users = int((await cursor.fetchone())[0] or 0)
                await cursor.execute("SELECT COUNT(*) FROM mail_accounts")
                accounts = int((await cursor.fetchone())[0] or 0)
                placeholders = ",".join("%s" for _ in _RUNNABLE_JOB_STATES)
                await cursor.execute(
                    f"SELECT COUNT(*) FROM worker_jobs WHERE status IN ({placeholders})",
                    _RUNNABLE_JOB_STATES,
                )
                runnable = int((await cursor.fetchone())[0] or 0)
                await cursor.execute("SELECT COUNT(*) FROM worker_jobs WHERE status = 'failed'")
                failed = int((await cursor.fetchone())[0] or 0)
                await cursor.execute(
                    "SELECT COUNT(*) FROM mail_operations WHERE status IN ('conflict', 'review_required')"
                )
                conflicts = int((await cursor.fetchone())[0] or 0)
            heartbeat = await RuntimeRepository(connection).latest_heartbeat("worker")
        return AdminDiagnosticsResponse(
            users=users,
            accounts=accounts,
            runnable_jobs=runnable,
            failed_jobs=failed,
            conflicts=conflicts,
            worker_heartbeat_at=heartbeat,
        )
