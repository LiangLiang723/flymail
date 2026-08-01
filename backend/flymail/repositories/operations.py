"""SQL-only persistence for local-first mail operations."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping

import aiomysql

from flymail.domain.ids import new_id
from flymail.domain.operations import OperationKind, OperationRecord
from flymail.repositories.base import TenantContext, fetch_all, fetch_one
from flymail.repositories.outbox import encode_safe_json, validate_safe_payload


_ACTIVE_STATUSES = ("pending", "applying", "retry_wait", "review_required", "conflict")
_MOTION_TYPES = tuple(
    kind.value
    for kind in (
        OperationKind.MOVE,
        OperationKind.ARCHIVE,
        OperationKind.TRASH,
        OperationKind.DELETE_PERMANENT,
    )
)


def _required_text(value: str, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{label} is required")
    return normalized


def _decode_json(value) -> dict[str, object]:
    if isinstance(value, dict):
        return dict(value)
    decoded = json.loads(str(value or "{}"))
    if not isinstance(decoded, dict):
        raise ValueError("operation desired state must be an object")
    return dict(decoded)


def _record(row: Mapping[str, object]) -> OperationRecord:
    return OperationRecord(
        id=str(row["id"]),
        user_uid=str(row["user_uid"]),
        operation_group_id=(
            str(row["operation_group_id"])
            if row.get("operation_group_id")
            else None
        ),
        kind=OperationKind(str(row["operation_type"])),
        target_type=str(row["target_type"]),
        target_id=str(row["target_id"]),
        account_id=str(row["account_id"]),
        remote_instance_id=str(row["remote_instance_id"]),
        desired_state=_decode_json(row["desired_state"]),
        observed_remote_version=str(row["observed_remote_version"] or ""),
        status=str(row["status"]),
        attempt_count=int(row["attempt_count"] or 0),
        idempotency_key=str(row["idempotency_key"]),
        created_at=float(row["created_at"] or 0),
        updated_at=float(row["updated_at"] or 0),
    )


class OperationRepository:
    """Persist operations on a caller-owned transaction."""

    def __init__(self, connection: aiomysql.Connection) -> None:
        self.connection = connection

    async def find_by_idempotency(
        self,
        tenant: TenantContext,
        idempotency_key: str,
        *,
        for_update: bool = False,
    ) -> OperationRecord | None:
        suffix = " FOR UPDATE" if for_update else ""
        row = await fetch_one(
            self.connection,
            """
            SELECT id, user_uid, operation_group_id, operation_type,
                   target_type, target_id, account_id, remote_instance_id,
                   desired_state, observed_remote_version, status,
                   attempt_count, idempotency_key, created_at, updated_at
            FROM mail_operations
            WHERE user_uid = %s AND idempotency_key = %s
            """ + suffix,
            (tenant.user_uid, _required_text(idempotency_key, "idempotency_key")),
        )
        return _record(row) if row else None

    async def get(
        self,
        tenant: TenantContext,
        operation_id: str,
        *,
        for_update: bool = False,
    ) -> OperationRecord | None:
        suffix = " FOR UPDATE" if for_update else ""
        row = await fetch_one(
            self.connection,
            """
            SELECT id, user_uid, operation_group_id, operation_type,
                   target_type, target_id, account_id, remote_instance_id,
                   desired_state, observed_remote_version, status,
                   attempt_count, idempotency_key, created_at, updated_at
            FROM mail_operations
            WHERE user_uid = %s AND id = %s
            """ + suffix,
            (tenant.user_uid, _required_text(operation_id, "operation_id")),
        )
        return _record(row) if row else None

    async def create(
        self,
        tenant: TenantContext,
        *,
        kind: OperationKind,
        target_type: str,
        target_id: str,
        account_id: str,
        remote_instance_id: str,
        desired_state: dict[str, object],
        observed_remote_version: str,
        idempotency_key: str,
        operation_group_id: str | None,
        priority: int,
        available_at: float,
        now: float,
    ) -> str:
        if not isinstance(kind, OperationKind):
            raise TypeError("kind must be OperationKind")
        if not isinstance(desired_state, dict):
            raise TypeError("desired_state must be dict")
        validate_safe_payload(desired_state, path="operation.desired_state")
        operation_id = new_id("op")
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO mail_operations (
                    id, user_uid, operation_group_id, operation_type,
                    target_type, target_id, account_id, remote_instance_id,
                    desired_state, observed_remote_version, status, priority,
                    available_at, attempt_count, last_error_class,
                    last_error_message, idempotency_key, created_at,
                    updated_at, completed_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                          'pending', %s, %s, 0, '', '', %s, %s, %s, NULL)
                """,
                (
                    operation_id,
                    tenant.user_uid,
                    operation_group_id,
                    kind.value,
                    _required_text(target_type, "target_type"),
                    _required_text(target_id, "target_id"),
                    _required_text(account_id, "account_id"),
                    _required_text(remote_instance_id, "remote_instance_id"),
                    encode_safe_json(desired_state),
                    str(observed_remote_version or ""),
                    int(priority),
                    float(available_at),
                    _required_text(idempotency_key, "idempotency_key"),
                    float(now),
                    float(now),
                ),
            )
        return operation_id

    async def supersede_pending_motion(
        self,
        tenant: TenantContext,
        remote_instance_id: str,
        *,
        exclude_operation_id: str | None,
        now: float,
    ) -> tuple[str, ...]:
        placeholders = ",".join("%s" for _ in _MOTION_TYPES)
        params: list[object] = [
            tenant.user_uid,
            _required_text(remote_instance_id, "remote_instance_id"),
            *_MOTION_TYPES,
        ]
        exclusion = ""
        if exclude_operation_id:
            exclusion = " AND id <> %s"
            params.append(exclude_operation_id)
        rows = await fetch_all(
            self.connection,
            f"""
            SELECT id
            FROM mail_operations
            WHERE user_uid = %s AND remote_instance_id = %s
              AND operation_type IN ({placeholders})
              AND status IN ('pending', 'retry_wait', 'review_required', 'conflict')
              {exclusion}
            FOR UPDATE
            """,
            tuple(params),
        )
        identifiers = tuple(str(row["id"]) for row in rows)
        if not identifiers:
            return ()
        id_placeholders = ",".join("%s" for _ in identifiers)
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                f"""
                UPDATE mail_operations
                SET status = 'cancelled', last_error_class = 'Superseded',
                    last_error_message = 'superseded by newer local intent',
                    updated_at = %s, completed_at = %s
                WHERE user_uid = %s AND id IN ({id_placeholders})
                """,
                (float(now), float(now), tenant.user_uid, *identifiers),
            )
        return identifiers

    async def mark_applying(
        self,
        tenant: TenantContext,
        operation_id: str,
        *,
        now: float,
    ) -> OperationRecord | None:
        record = await self.get(tenant, operation_id, for_update=True)
        if record is None or record.status not in {"pending", "retry_wait"}:
            return None
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                """
                UPDATE mail_operations
                SET status = 'applying', attempt_count = attempt_count + 1,
                    updated_at = %s, last_error_class = '',
                    last_error_message = ''
                WHERE user_uid = %s AND id = %s
                  AND status IN ('pending', 'retry_wait')
                """,
                (float(now), tenant.user_uid, operation_id),
            )
            if cursor.rowcount != 1:
                return None
        return OperationRecord(
            id=record.id,
            user_uid=record.user_uid,
            operation_group_id=record.operation_group_id,
            kind=record.kind,
            target_type=record.target_type,
            target_id=record.target_id,
            account_id=record.account_id,
            remote_instance_id=record.remote_instance_id,
            desired_state=record.desired_state,
            observed_remote_version=record.observed_remote_version,
            status="applying",
            attempt_count=record.attempt_count + 1,
            idempotency_key=record.idempotency_key,
            created_at=record.created_at,
            updated_at=float(now),
        )

    async def finish(
        self,
        tenant: TenantContext,
        operation_id: str,
        *,
        status: str,
        error_class: str,
        error_message: str,
        now: float,
        completed: bool,
    ) -> bool:
        allowed = {"synced", "retry_wait", "review_required", "conflict", "failed", "cancelled"}
        normalized_status = str(status or "").strip()
        if normalized_status not in allowed:
            raise ValueError("unsupported operation finish status")
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                """
                UPDATE mail_operations
                SET status = %s, last_error_class = %s,
                    last_error_message = %s, updated_at = %s,
                    completed_at = %s
                WHERE user_uid = %s AND id = %s
                  AND status IN ('pending', 'applying', 'retry_wait',
                                 'review_required', 'conflict')
                """,
                (
                    normalized_status,
                    str(error_class or "")[:96],
                    str(error_message or "")[:512],
                    float(now),
                    float(now) if completed else None,
                    tenant.user_uid,
                    _required_text(operation_id, "operation_id"),
                ),
            )
            return cursor.rowcount == 1

    async def group_records(
        self,
        tenant: TenantContext,
        operation_group_id: str,
    ) -> tuple[OperationRecord, ...]:
        rows = await fetch_all(
            self.connection,
            """
            SELECT id, user_uid, operation_group_id, operation_type,
                   target_type, target_id, account_id, remote_instance_id,
                   desired_state, observed_remote_version, status,
                   attempt_count, idempotency_key, created_at, updated_at
            FROM mail_operations
            WHERE user_uid = %s AND operation_group_id = %s
            ORDER BY id
            """,
            (
                tenant.user_uid,
                _required_text(operation_group_id, "operation_group_id"),
            ),
        )
        return tuple(_record(row) for row in rows)

    async def active_for_remote(
        self,
        tenant: TenantContext,
        remote_instance_ids: Iterable[str],
    ) -> tuple[OperationRecord, ...]:
        identifiers = tuple(
            sorted(
                {
                    str(value or "").strip()
                    for value in remote_instance_ids
                    if str(value or "").strip()
                }
            )
        )
        if not identifiers:
            return ()
        placeholders = ",".join("%s" for _ in identifiers)
        statuses = ",".join("%s" for _ in _ACTIVE_STATUSES)
        rows = await fetch_all(
            self.connection,
            f"""
            SELECT id, user_uid, operation_group_id, operation_type,
                   target_type, target_id, account_id, remote_instance_id,
                   desired_state, observed_remote_version, status,
                   attempt_count, idempotency_key, created_at, updated_at
            FROM mail_operations
            WHERE user_uid = %s
              AND remote_instance_id IN ({placeholders})
              AND status IN ({statuses})
            ORDER BY created_at, id
            """,
            (tenant.user_uid, *identifiers, *_ACTIVE_STATUSES),
        )
        return tuple(_record(row) for row in rows)
