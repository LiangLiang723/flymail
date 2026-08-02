"""Resumable bounded Worker for query-scoped local mail operations."""

from __future__ import annotations

import json
from collections.abc import Mapping

import aiomysql

from flymail.domain.operations import OperationKind
from flymail.infrastructure.db.pool import DatabasePool
from flymail.providers.registry import ProviderRegistry
from flymail.repositories.base import TenantContext
from flymail.repositories.outbox import OutboxRepository
from flymail.workers.dispatcher import JobContext, JobOutcome
from flymail.workers.operation_apply import OperationService


class BulkMarkReadHandler:
    """Process a persisted mark-all-read filter in deterministic bounded batches."""

    def __init__(
        self,
        pool: DatabasePool,
        registry: ProviderRegistry | None = None,
        *,
        batch_size: int = 100,
    ) -> None:
        if not isinstance(pool, DatabasePool):
            raise TypeError("pool must be DatabasePool")
        size = int(batch_size)
        if size < 1 or size > 500:
            raise ValueError("batch_size must be between 1 and 500")
        self.pool = pool
        self.core = OperationService(pool, registry or ProviderRegistry.default())
        self.batch_size = size

    async def __call__(
        self,
        context: JobContext,
        payload: Mapping[str, object],
    ) -> JobOutcome:
        bulk_id = str(payload.get("bulk_operation_id") or "").strip()
        if not bulk_id or not context.user_uid:
            return JobOutcome.fail(
                "InvalidBulkOperationJob",
                "bulk operation job scope is invalid",
            )
        tenant = TenantContext(context.user_uid)
        row = await self._load(tenant, bulk_id)
        if row is None:
            return JobOutcome.fail(
                "BulkOperationNotFound",
                "bulk operation does not exist",
            )
        if str(row["status"]) == "completed":
            return JobOutcome.success()
        if str(row["operation_type"]) != "set_read":
            return JobOutcome.fail(
                "UnsupportedBulkOperation",
                "bulk operation type is unsupported",
            )
        raw_filter = row["filter_json"]
        if isinstance(raw_filter, str):
            raw_filter = json.loads(raw_filter)
        if not isinstance(raw_filter, dict):
            return JobOutcome.fail(
                "InvalidBulkOperationFilter",
                "bulk operation filter is invalid",
            )
        remote_ids = await self._next_batch(
            tenant,
            raw_filter,
            str(row["cursor_remote_id"] or ""),
        )
        if not remote_ids:
            await self._complete(tenant, bulk_id)
            return JobOutcome.success()

        operation_ids: list[str] = []
        for remote_id in remote_ids:
            if context.stop_event.is_set():
                return JobOutcome.retry(
                    "WorkerStopping",
                    "bulk operation paused for shutdown",
                    base_seconds=0,
                    max_seconds=0,
                )
            operation_ids.append(
                await self.core.record_local_intent(
                    tenant,
                    remote_instance_id=remote_id,
                    kind=OperationKind.SET_READ,
                    desired_state={"value": True},
                    idempotency_key=f"bulk:{bulk_id}:{remote_id}",
                )
            )
        await self._advance(
            tenant,
            bulk_id,
            cursor_remote_id=remote_ids[-1],
            matched_count=len(remote_ids),
            operation_count=len(operation_ids),
        )
        return JobOutcome.retry(
            "BulkOperationContinuation",
            "bulk operation has more rows to inspect",
            base_seconds=0,
            max_seconds=0,
        )

    async def _load(self, tenant: TenantContext, bulk_id: str) -> dict | None:
        async with self.pool.acquire() as connection:
            async with connection.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(
                    """
                    SELECT id, operation_type, filter_json,
                           cursor_remote_id, status
                    FROM bulk_mail_operations
                    WHERE id = %s AND user_uid = %s
                    """,
                    (bulk_id, tenant.user_uid),
                )
                row = await cursor.fetchone()
        return dict(row) if row else None

    async def _next_batch(
        self,
        tenant: TenantContext,
        snapshot: dict,
        cursor_remote_id: str,
    ) -> tuple[str, ...]:
        conditions = [
            "ri.user_uid = %s",
            "ri.remote_deleted = 0",
            "ri.is_read = 0",
            "mb.semantic_key = %s",
            "a.status = 'active'",
            "ri.id > %s",
        ]
        params: list[object] = [
            tenant.user_uid,
            str(snapshot.get("semantic_mailbox") or "inbox").casefold(),
            cursor_remote_id,
        ]
        account_id = str(snapshot.get("account_id") or "").strip()
        if account_id:
            conditions.append("ri.account_id = %s")
            params.append(account_id)
        native_label = str(snapshot.get("native_label") or "").strip()
        if native_label:
            conditions.append(
                """
                EXISTS (
                    SELECT 1
                    FROM message_memberships label_membership
                    JOIN mailboxes label_mailbox
                      ON label_mailbox.id = label_membership.mailbox_id
                     AND label_mailbox.user_uid = label_membership.user_uid
                    WHERE label_membership.user_uid = ri.user_uid
                      AND label_membership.remote_instance_id = ri.id
                      AND label_mailbox.id = %s
                      AND label_mailbox.mailbox_type = 'label'
                )
                """
            )
            params.append(native_label)
        params.append(self.batch_size)
        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    f"""
                    SELECT DISTINCT ri.id
                    FROM message_remote_instances ri
                    JOIN message_memberships membership
                      ON membership.remote_instance_id = ri.id
                     AND membership.user_uid = ri.user_uid
                    JOIN mailboxes mb
                      ON mb.id = membership.mailbox_id
                     AND mb.user_uid = membership.user_uid
                    JOIN mail_accounts a
                      ON a.id = ri.account_id AND a.user_uid = ri.user_uid
                    WHERE {' AND '.join(conditions)}
                    ORDER BY ri.id
                    LIMIT %s
                    """,
                    tuple(params),
                )
                rows = await cursor.fetchall()
        return tuple(str(row[0]) for row in rows)

    async def _advance(
        self,
        tenant: TenantContext,
        bulk_id: str,
        *,
        cursor_remote_id: str,
        matched_count: int,
        operation_count: int,
    ) -> None:
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        UPDATE bulk_mail_operations
                        SET cursor_remote_id = %s, status = 'running',
                            matched_count = matched_count + %s,
                            operation_count = operation_count + %s,
                            updated_at = UNIX_TIMESTAMP(UTC_TIMESTAMP(6))
                        WHERE id = %s AND user_uid = %s
                          AND status IN ('pending', 'running')
                        """,
                        (
                            cursor_remote_id,
                            int(matched_count),
                            int(operation_count),
                            bulk_id,
                            tenant.user_uid,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise RuntimeError("bulk operation could not advance")
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise

    async def _complete(self, tenant: TenantContext, bulk_id: str) -> None:
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        UPDATE bulk_mail_operations
                        SET status = 'completed',
                            completed_at = UNIX_TIMESTAMP(UTC_TIMESTAMP(6)),
                            updated_at = UNIX_TIMESTAMP(UTC_TIMESTAMP(6))
                        WHERE id = %s AND user_uid = %s
                          AND status IN ('pending', 'running')
                        """,
                        (bulk_id, tenant.user_uid),
                    )
                    changed = cursor.rowcount == 1
                if changed:
                    await OutboxRepository(connection, tenant).append(
                        "mail.bulk_operation.completed",
                        bulk_id,
                        {"bulk_operation_id": bulk_id},
                        aggregate_type="bulk_mail_operation",
                    )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
