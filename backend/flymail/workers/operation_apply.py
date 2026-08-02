"""Local-first operation recording, conflict handling, and remote application."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

import aiomysql

from flymail.domain.errors import ConflictError, NotFoundError, PermanentError, RetryableError
from flymail.domain.ids import new_id
from flymail.domain.operations import (
    MOTION_KINDS,
    REVERSIBLE_KINDS,
    OperationApplySummary,
    OperationGroupResult,
    OperationKind,
    OperationRecord,
    RemoteApplyResult,
    RemoteOperationCommand,
    RemoteOperationState,
)
from flymail.infrastructure.db.pool import DatabasePool
from flymail.infrastructure.db.uow import SqlUnitOfWork
from flymail.providers.registry import ProviderRegistry
from flymail.repositories.base import TenantContext, fetch_all, fetch_one
from flymail.repositories.jobs import JobRepository, JobSpec
from flymail.repositories.operations import OperationRepository
from flymail.repositories.outbox import OutboxRepository
from flymail.repositories.threads import ThreadRepository
from flymail.workers.dispatcher import JobContext, JobOutcome


@dataclass(frozen=True, slots=True)
class _Membership:
    mailbox_id: str
    native_key: str
    semantic_key: str
    membership_kind: str


@dataclass(frozen=True, slots=True)
class _LocalRemote:
    remote_instance_id: str
    account_id: str
    provider_key: str
    message_id: str
    thread_id: str
    remote_version: str
    is_read: bool
    is_starred: bool
    remote_deleted: bool
    memberships: tuple[_Membership, ...]


class RemoteOperationGateway(Protocol):
    async def observe(self, operation: OperationRecord) -> RemoteOperationState | None: ...

    async def apply(self, command: RemoteOperationCommand) -> RemoteApplyResult: ...


class OperationService:
    def __init__(self, pool: DatabasePool, registry: ProviderRegistry) -> None:
        if not isinstance(registry, ProviderRegistry):
            raise TypeError("registry must be ProviderRegistry")
        self.pool = pool
        self.registry = registry

    async def record_local_intent(
        self,
        tenant: TenantContext,
        *,
        remote_instance_id: str,
        kind: OperationKind,
        desired_state: dict[str, object],
        idempotency_key: str,
        confirm_permanent: bool = False,
        now: float | None = None,
    ) -> str:
        timestamp = float(time.time() if now is None else now)
        async with SqlUnitOfWork(self.pool) as uow:
            connection = self._connection(uow)
            await self._lock_enabled_user(connection, tenant)
            operation_id, thread_id = await self._record_one(
                connection,
                tenant,
                remote_instance_id=remote_instance_id,
                kind=kind,
                desired_state=desired_state,
                idempotency_key=idempotency_key,
                operation_group_id=None,
                confirm_permanent=confirm_permanent,
                now=timestamp,
            )
            await ThreadRepository(connection).refresh_projections(
                tenant,
                (thread_id,),
                now=timestamp,
            )
            await uow.commit()
            return operation_id

    async def record_thread_intent(
        self,
        tenant: TenantContext,
        *,
        thread_id: str,
        kind: OperationKind,
        desired_state: dict[str, object],
        idempotency_key: str,
        confirm_permanent: bool = False,
        now: float | None = None,
    ) -> OperationGroupResult:
        timestamp = float(time.time() if now is None else now)
        normalized_thread = self._required_text(thread_id, "thread_id")
        normalized_idempotency = self._required_text(
            idempotency_key,
            "idempotency_key",
        )
        async with SqlUnitOfWork(self.pool) as uow:
            connection = self._connection(uow)
            await self._lock_enabled_user(connection, tenant)
            rows = await fetch_all(
                connection,
                """
                SELECT DISTINCT ri.id
                FROM thread_messages tm
                JOIN message_remote_instances ri
                  ON ri.message_id = tm.message_id
                 AND ri.user_uid = tm.user_uid
                JOIN mail_accounts a
                  ON a.id = ri.account_id AND a.user_uid = ri.user_uid
                WHERE tm.user_uid = %s AND tm.thread_id = %s
                  AND ri.remote_deleted = 0 AND a.status = 'active'
                ORDER BY ri.id
                FOR UPDATE
                """,
                (tenant.user_uid, normalized_thread),
            )
            if not rows:
                raise NotFoundError("thread has no active remote messages")
            remote_ids = tuple(str(row["id"]) for row in rows)
            repository = OperationRepository(connection)
            existing = []
            for remote_id in remote_ids:
                record = await repository.find_by_idempotency(
                    tenant,
                    f"{normalized_idempotency}:{remote_id}",
                    for_update=True,
                )
                if record is not None:
                    existing.append(record)
            if existing:
                if len(existing) != len(remote_ids):
                    raise ConflictError("operation group is only partially persisted")
                group_ids = {
                    record.operation_group_id
                    for record in existing
                    if record.operation_group_id
                }
                if len(group_ids) != 1:
                    raise ConflictError("operation group identity is inconsistent")
                by_remote = {record.remote_instance_id: record for record in existing}
                for remote_id in remote_ids:
                    record = by_remote.get(remote_id)
                    if record is None or record.kind is not kind or not self._intent_matches(
                        record,
                        desired_state,
                        confirm_permanent=confirm_permanent,
                    ):
                        raise ConflictError("idempotency key belongs to another operation intent")
                await uow.commit()
                return OperationGroupResult(
                    operation_group_id=next(iter(group_ids)),
                    operation_ids=tuple(by_remote[remote_id].id for remote_id in remote_ids),
                )

            group_id = new_id("opgrp")
            operation_ids: list[str] = []
            for remote_id in remote_ids:
                operation_id, observed_thread = await self._record_one(
                    connection,
                    tenant,
                    remote_instance_id=remote_id,
                    kind=kind,
                    desired_state=desired_state,
                    idempotency_key=f"{normalized_idempotency}:{remote_id}",
                    operation_group_id=group_id,
                    confirm_permanent=confirm_permanent,
                    now=timestamp,
                )
                if observed_thread != normalized_thread:
                    raise ConflictError("remote message moved to another thread")
                operation_ids.append(operation_id)
            await OutboxRepository(connection, tenant).append(
                "mail.operation.group.pending",
                group_id,
                {
                    "operation_group_id": group_id,
                    "thread_id": normalized_thread,
                    "operation_ids": operation_ids,
                },
                aggregate_type="mail_operation_group",
                now=timestamp,
            )
            await ThreadRepository(connection).refresh_projections(
                tenant,
                (normalized_thread,),
                now=timestamp,
            )
            await uow.commit()
        return OperationGroupResult(
            operation_group_id=group_id,
            operation_ids=tuple(operation_ids),
        )

    async def undo(
        self,
        tenant: TenantContext,
        operation_id: str,
        *,
        idempotency_key: str,
        now: float | None = None,
    ) -> str:
        timestamp = float(time.time() if now is None else now)
        async with SqlUnitOfWork(self.pool) as uow:
            connection = self._connection(uow)
            await self._lock_enabled_user(connection, tenant)
            repository = OperationRepository(connection)
            operation = await repository.get(
                tenant,
                operation_id,
                for_update=True,
            )
            if operation is None:
                raise NotFoundError("operation was not found")
            if operation.kind is OperationKind.DELETE_PERMANENT:
                raise ConflictError("permanent delete cannot be undone")
            if operation.kind not in REVERSIBLE_KINDS:
                raise ConflictError("operation is not reversible")

            if operation.status in {
                "pending",
                "retry_wait",
                "review_required",
                "conflict",
            }:
                local = await self._load_remote(
                    connection,
                    tenant,
                    operation.remote_instance_id,
                    for_update=True,
                    allow_deleted=True,
                )
                await self._restore_previous(
                    connection,
                    tenant,
                    local,
                    operation,
                    timestamp,
                )
                await repository.finish(
                    tenant,
                    operation.id,
                    status="cancelled",
                    error_class="UserCancelled",
                    error_message="cancelled before remote confirmation",
                    now=timestamp,
                    completed=True,
                )
                await ThreadRepository(connection).refresh_projections(
                    tenant,
                    (local.thread_id,),
                    now=timestamp,
                )
                await OutboxRepository(connection, tenant).append(
                    "mail.operation.cancelled",
                    operation.id,
                    {"operation_id": operation.id, "thread_id": local.thread_id},
                    aggregate_type="mail_operation",
                    now=timestamp,
                )
                await uow.commit()
                return operation.id

            if operation.status != "synced":
                raise ConflictError("operation cannot be undone in its current state")
            compensation_kind, compensation_state = self._compensation(operation)
            compensation_id, thread_id = await self._record_one(
                connection,
                tenant,
                remote_instance_id=operation.remote_instance_id,
                kind=compensation_kind,
                desired_state=compensation_state,
                idempotency_key=idempotency_key,
                operation_group_id=None,
                confirm_permanent=False,
                now=timestamp,
            )
            await ThreadRepository(connection).refresh_projections(
                tenant,
                (thread_id,),
                now=timestamp,
            )
            await uow.commit()
            return compensation_id

    async def _record_one(
        self,
        connection: aiomysql.Connection,
        tenant: TenantContext,
        *,
        remote_instance_id: str,
        kind: OperationKind,
        desired_state: dict[str, object],
        idempotency_key: str,
        operation_group_id: str | None,
        confirm_permanent: bool,
        now: float,
    ) -> tuple[str, str]:
        if not isinstance(kind, OperationKind):
            raise TypeError("kind must be OperationKind")
        if not isinstance(desired_state, dict):
            raise TypeError("desired_state must be dict")
        repository = OperationRepository(connection)
        existing = await repository.find_by_idempotency(
            tenant,
            idempotency_key,
            for_update=True,
        )
        if existing is not None:
            if (
                existing.remote_instance_id != remote_instance_id
                or existing.kind is not kind
                or not self._intent_matches(
                    existing,
                    desired_state,
                    confirm_permanent=confirm_permanent,
                )
            ):
                raise ConflictError("idempotency key belongs to another operation intent")
            return existing.id, existing.target_id

        local = await self._load_remote(
            connection,
            tenant,
            remote_instance_id,
            for_update=True,
        )
        if kind is OperationKind.DELETE_PERMANENT:
            active = await repository.active_for_remote(
                tenant,
                (local.remote_instance_id,),
            )
            if any(operation.kind in MOTION_KINDS for operation in active):
                raise ConflictError(
                    "pending mailbox operation must finish before permanent delete"
                )
        elif kind in MOTION_KINDS:
            await repository.supersede_pending_motion(
                tenant,
                local.remote_instance_id,
                exclude_operation_id=None,
                now=now,
            )
        normalized = await self._apply_local_intent(
            connection,
            tenant,
            local,
            kind,
            desired_state,
            confirm_permanent=confirm_permanent,
            now=now,
        )
        operation_id = await repository.create(
            tenant,
            kind=kind,
            target_type="thread",
            target_id=local.thread_id,
            account_id=local.account_id,
            remote_instance_id=local.remote_instance_id,
            desired_state=normalized,
            observed_remote_version=local.remote_version,
            idempotency_key=idempotency_key,
            operation_group_id=operation_group_id,
            priority=20,
            available_at=now,
            now=now,
        )
        await OutboxRepository(connection, tenant).append(
            "mail.operation.pending",
            operation_id,
            {
                "operation_id": operation_id,
                "operation_type": kind.value,
                "thread_id": local.thread_id,
                "remote_instance_id": local.remote_instance_id,
            },
            aggregate_type="mail_operation",
            now=now,
        )
        await JobRepository(connection).enqueue(
            JobSpec(
                queue_name="operations",
                job_kind="mail.operation.apply",
                payload={"operation_id": operation_id},
                user_uid=tenant.user_uid,
                account_id=local.account_id,
                provider_key=local.provider_key,
                priority=20,
                available_at=now,
                max_attempts=20,
                dedupe_key=f"mail-operation:{operation_id}",
            ),
            now=now,
        )
        return operation_id, local.thread_id

    @staticmethod
    def _intent_matches(
        existing: OperationRecord,
        requested: dict[str, object],
        *,
        confirm_permanent: bool,
    ) -> bool:
        if existing.kind in {OperationKind.SET_READ, OperationKind.SET_STARRED}:
            value = requested.get("value")
            return isinstance(value, bool) and existing.desired_state.get("value") is value
        if existing.kind in {
            OperationKind.ADD_LABEL,
            OperationKind.REMOVE_LABEL,
            OperationKind.MOVE,
        }:
            requested_mailbox = str(requested.get("mailbox_id") or "").strip()
            return bool(requested_mailbox) and (
                str(existing.desired_state.get("target_mailbox_id") or "")
                == requested_mailbox
            )
        if existing.kind is OperationKind.DELETE_PERMANENT:
            return bool(confirm_permanent) and bool(existing.desired_state.get("confirmed"))
        return not requested

    async def _apply_local_intent(
        self,
        connection: aiomysql.Connection,
        tenant: TenantContext,
        local: _LocalRemote,
        kind: OperationKind,
        desired: dict[str, object],
        *,
        confirm_permanent: bool,
        now: float,
    ) -> dict[str, object]:
        previous = {
            "is_read": local.is_read,
            "is_starred": local.is_starred,
            "memberships": [
                {
                    "mailbox_id": item.mailbox_id,
                    "native_key": item.native_key,
                    "semantic_key": item.semantic_key,
                    "membership_kind": item.membership_kind,
                }
                for item in local.memberships
            ],
            "remote_deleted": local.remote_deleted,
        }
        result: dict[str, object] = {
            "previous": previous,
            "remote_action": kind.value,
        }
        async with connection.cursor() as cursor:
            if kind in {OperationKind.SET_READ, OperationKind.SET_STARRED}:
                value = desired.get("value")
                if not isinstance(value, bool):
                    raise ValueError("field operation requires boolean value")
                column = "is_read" if kind is OperationKind.SET_READ else "is_starred"
                await cursor.execute(
                    f"""
                    UPDATE message_remote_instances
                    SET {column} = %s, updated_at = %s
                    WHERE user_uid = %s AND id = %s AND remote_deleted = 0
                    """,
                    (1 if value else 0, now, tenant.user_uid, local.remote_instance_id),
                )
                result["value"] = value
                return result

            if kind in {OperationKind.ADD_LABEL, OperationKind.REMOVE_LABEL}:
                target = await self._mailbox(
                    connection,
                    tenant,
                    local.account_id,
                    self._required_text(str(desired.get("mailbox_id") or ""), "mailbox_id"),
                )
                if target.membership_kind != "label":
                    raise ConflictError("label operation requires a label mailbox")
                result.update(
                    {
                        "target_mailbox_id": target.mailbox_id,
                        "target_native_key": target.native_key,
                        "remote_action": kind.value,
                    }
                )
                if kind is OperationKind.ADD_LABEL:
                    await self._insert_membership(connection, tenant, local.remote_instance_id, target, now)
                else:
                    await cursor.execute(
                        """
                        DELETE FROM message_memberships
                        WHERE user_uid = %s AND remote_instance_id = %s
                          AND mailbox_id = %s
                        """,
                        (tenant.user_uid, local.remote_instance_id, target.mailbox_id),
                    )
                return result

            if kind is OperationKind.ARCHIVE and local.provider_key == "gmail":
                target = next(
                    (item for item in local.memberships if item.semantic_key == "inbox"),
                    None,
                )
                if target is None:
                    raise ConflictError("message is not in Gmail Inbox")
                result.update(
                    {
                        "target_mailbox_id": target.mailbox_id,
                        "target_native_key": target.native_key,
                        "remote_action": "remove_label",
                    }
                )
                await cursor.execute(
                    """
                    DELETE FROM message_memberships
                    WHERE user_uid = %s AND remote_instance_id = %s
                      AND mailbox_id = %s
                    """,
                    (tenant.user_uid, local.remote_instance_id, target.mailbox_id),
                )
                return result

            if kind is OperationKind.DELETE_PERMANENT:
                if not confirm_permanent:
                    raise ConflictError("permanent delete requires explicit confirmation")
                if not any(item.semantic_key == "trash" for item in local.memberships):
                    raise ConflictError("message must be in trash before permanent delete")
                result.update({"confirmed": True, "remote_action": "delete_permanent"})
                await cursor.execute(
                    "DELETE FROM message_memberships WHERE user_uid = %s AND remote_instance_id = %s",
                    (tenant.user_uid, local.remote_instance_id),
                )
                await cursor.execute(
                    """
                    UPDATE message_remote_instances
                    SET remote_deleted = 1, updated_at = %s
                    WHERE user_uid = %s AND id = %s
                    """,
                    (now, tenant.user_uid, local.remote_instance_id),
                )
                return result

            if kind is OperationKind.MOVE:
                target = await self._mailbox(
                    connection,
                    tenant,
                    local.account_id,
                    self._required_text(str(desired.get("mailbox_id") or ""), "mailbox_id"),
                )
            else:
                semantic = "archive" if kind is OperationKind.ARCHIVE else "trash"
                target = await self._semantic_mailbox(
                    connection,
                    tenant,
                    local.account_id,
                    semantic,
                )
            result.update(
                {
                    "target_mailbox_id": target.mailbox_id,
                    "target_native_key": target.native_key,
                    "remote_action": "move",
                    "allow_copy_delete": bool(
                        not self.registry.get(local.provider_key).capabilities().supports_move
                        and self.registry.get(local.provider_key).capabilities().supports_uidplus
                    ),
                }
            )
            await cursor.execute(
                """
                DELETE membership
                FROM message_memberships AS membership
                JOIN mailboxes AS mailbox
                  ON mailbox.id = membership.mailbox_id
                 AND mailbox.user_uid = membership.user_uid
                WHERE membership.user_uid = %s
                  AND membership.remote_instance_id = %s
                  AND mailbox.mailbox_type = 'folder'
                """,
                (tenant.user_uid, local.remote_instance_id),
            )
            await self._insert_membership(
                connection,
                tenant,
                local.remote_instance_id,
                target,
                now,
            )
            return result

    async def _restore_previous(
        self,
        connection: aiomysql.Connection,
        tenant: TenantContext,
        local: _LocalRemote,
        operation: OperationRecord,
        now: float,
    ) -> None:
        previous = operation.desired_state.get("previous")
        if not isinstance(previous, dict):
            raise ConflictError("operation does not contain restorable state")
        async with connection.cursor() as cursor:
            if operation.kind is OperationKind.SET_READ:
                await cursor.execute(
                    """
                    UPDATE message_remote_instances
                    SET is_read = %s, updated_at = %s
                    WHERE user_uid = %s AND id = %s
                    """,
                    (1 if bool(previous.get("is_read")) else 0, now, tenant.user_uid, local.remote_instance_id),
                )
                return
            if operation.kind is OperationKind.SET_STARRED:
                await cursor.execute(
                    """
                    UPDATE message_remote_instances
                    SET is_starred = %s, updated_at = %s
                    WHERE user_uid = %s AND id = %s
                    """,
                    (1 if bool(previous.get("is_starred")) else 0, now, tenant.user_uid, local.remote_instance_id),
                )
                return
            await cursor.execute(
                "DELETE FROM message_memberships WHERE user_uid = %s AND remote_instance_id = %s",
                (tenant.user_uid, local.remote_instance_id),
            )
            memberships = previous.get("memberships")
            if not isinstance(memberships, list):
                raise ConflictError("operation memberships cannot be restored")
            for item in memberships:
                if not isinstance(item, dict):
                    continue
                target = _Membership(
                    mailbox_id=self._required_text(str(item.get("mailbox_id") or ""), "mailbox_id"),
                    native_key=str(item.get("native_key") or ""),
                    semantic_key=str(item.get("semantic_key") or "custom"),
                    membership_kind=str(item.get("membership_kind") or "folder"),
                )
                await self._insert_membership(connection, tenant, local.remote_instance_id, target, now)
            await cursor.execute(
                """
                UPDATE message_remote_instances
                SET remote_deleted = %s, updated_at = %s
                WHERE user_uid = %s AND id = %s
                """,
                (1 if bool(previous.get("remote_deleted")) else 0, now, tenant.user_uid, local.remote_instance_id),
            )

    def _compensation(self, operation: OperationRecord) -> tuple[OperationKind, dict[str, object]]:
        previous = operation.desired_state.get("previous")
        if not isinstance(previous, dict):
            raise ConflictError("operation does not contain compensating state")
        if operation.kind is OperationKind.SET_READ:
            return OperationKind.SET_READ, {"value": bool(previous.get("is_read"))}
        if operation.kind is OperationKind.SET_STARRED:
            return OperationKind.SET_STARRED, {"value": bool(previous.get("is_starred"))}
        if operation.kind is OperationKind.ADD_LABEL:
            return OperationKind.REMOVE_LABEL, {
                "mailbox_id": operation.desired_state.get("target_mailbox_id")
            }
        if operation.kind is OperationKind.REMOVE_LABEL:
            return OperationKind.ADD_LABEL, {
                "mailbox_id": operation.desired_state.get("target_mailbox_id")
            }
        memberships = previous.get("memberships")
        if not isinstance(memberships, list) or not memberships:
            raise ConflictError("operation has no previous mailbox")
        first = memberships[0]
        if not isinstance(first, dict):
            raise ConflictError("operation has invalid previous mailbox")
        return OperationKind.MOVE, {"mailbox_id": first.get("mailbox_id")}

    async def _load_remote(
        self,
        connection: aiomysql.Connection,
        tenant: TenantContext,
        remote_instance_id: str,
        *,
        for_update: bool,
        allow_deleted: bool = False,
    ) -> _LocalRemote:
        suffix = " FOR UPDATE" if for_update else ""
        deleted_clause = "" if allow_deleted else " AND ri.remote_deleted = 0"
        row = await fetch_one(
            connection,
            """
            SELECT ri.id, ri.account_id, a.provider_key, ri.message_id,
                   m.thread_id, ri.remote_version, ri.is_read,
                   ri.is_starred, ri.remote_deleted
            FROM message_remote_instances ri
            JOIN messages m
              ON m.id = ri.message_id AND m.user_uid = ri.user_uid
            JOIN mail_accounts a
              ON a.id = ri.account_id AND a.user_uid = ri.user_uid
            WHERE ri.user_uid = %s AND ri.id = %s
              AND a.status = 'active'
            """ + deleted_clause + suffix,
            (tenant.user_uid, self._required_text(remote_instance_id, "remote_instance_id")),
        )
        if row is None:
            raise NotFoundError("remote message instance was not found")
        membership_rows = await fetch_all(
            connection,
            """
            SELECT membership.mailbox_id, mailbox.native_key,
                   mailbox.semantic_key, membership.membership_kind
            FROM message_memberships membership
            JOIN mailboxes mailbox
              ON mailbox.id = membership.mailbox_id
             AND mailbox.user_uid = membership.user_uid
            WHERE membership.user_uid = %s
              AND membership.remote_instance_id = %s
            ORDER BY membership.mailbox_id
            """ + suffix,
            (tenant.user_uid, remote_instance_id),
        )
        return _LocalRemote(
            remote_instance_id=str(row["id"]),
            account_id=str(row["account_id"]),
            provider_key=str(row["provider_key"]),
            message_id=str(row["message_id"]),
            thread_id=str(row["thread_id"]),
            remote_version=str(row["remote_version"] or ""),
            is_read=bool(row["is_read"]),
            is_starred=bool(row["is_starred"]),
            remote_deleted=bool(row["remote_deleted"]),
            memberships=tuple(
                _Membership(
                    mailbox_id=str(item["mailbox_id"]),
                    native_key=str(item["native_key"]),
                    semantic_key=str(item["semantic_key"]),
                    membership_kind=str(item["membership_kind"]),
                )
                for item in membership_rows
            ),
        )

    async def _mailbox(
        self,
        connection: aiomysql.Connection,
        tenant: TenantContext,
        account_id: str,
        mailbox_id: str,
    ) -> _Membership:
        row = await fetch_one(
            connection,
            """
            SELECT id, native_key, semantic_key, mailbox_type
            FROM mailboxes
            WHERE user_uid = %s AND account_id = %s AND id = %s
            """,
            (tenant.user_uid, account_id, mailbox_id),
        )
        if row is None:
            raise NotFoundError("target mailbox was not found")
        return _Membership(
            mailbox_id=str(row["id"]),
            native_key=str(row["native_key"]),
            semantic_key=str(row["semantic_key"]),
            membership_kind=str(row["mailbox_type"]),
        )

    async def _semantic_mailbox(
        self,
        connection: aiomysql.Connection,
        tenant: TenantContext,
        account_id: str,
        semantic_key: str,
    ) -> _Membership:
        row = await fetch_one(
            connection,
            """
            SELECT id, native_key, semantic_key, mailbox_type
            FROM mailboxes
            WHERE user_uid = %s AND account_id = %s
              AND semantic_key = %s
            ORDER BY id
            LIMIT 1
            """,
            (tenant.user_uid, account_id, semantic_key),
        )
        if row is None:
            raise ConflictError(f"{semantic_key} mailbox is not mapped")
        return _Membership(
            mailbox_id=str(row["id"]),
            native_key=str(row["native_key"]),
            semantic_key=str(row["semantic_key"]),
            membership_kind=str(row["mailbox_type"]),
        )

    async def _insert_membership(
        self,
        connection: aiomysql.Connection,
        tenant: TenantContext,
        remote_instance_id: str,
        target: _Membership,
        now: float,
    ) -> None:
        row = await fetch_one(
            connection,
            """
            SELECT remote_instance_id
            FROM message_memberships
            WHERE user_uid = %s AND remote_instance_id = %s
              AND mailbox_id = %s
            FOR UPDATE
            """,
            (tenant.user_uid, remote_instance_id, target.mailbox_id),
        )
        if row is not None:
            return
        async with connection.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO message_memberships (
                    remote_instance_id, mailbox_id, user_uid,
                    membership_kind, provider_label,
                    created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    remote_instance_id,
                    target.mailbox_id,
                    tenant.user_uid,
                    target.membership_kind,
                    target.native_key if target.membership_kind == "label" else "",
                    now,
                    now,
                ),
            )

    async def _lock_enabled_user(
        self,
        connection: aiomysql.Connection,
        tenant: TenantContext,
    ) -> None:
        row = await fetch_one(
            connection,
            "SELECT id FROM users WHERE id = %s AND enabled = 1 FOR UPDATE",
            (tenant.user_uid,),
        )
        if row is None:
            raise NotFoundError("user was not found")

    @staticmethod
    def _connection(uow: SqlUnitOfWork) -> aiomysql.Connection:
        if uow.connection is None:
            raise RuntimeError("unit of work connection is unavailable")
        return uow.connection

    @staticmethod
    def _required_text(value: str, label: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError(f"{label} is required")
        return normalized


class OperationApplyHandler:
    def __init__(
        self,
        pool: DatabasePool,
        gateway: RemoteOperationGateway,
        registry: ProviderRegistry,
    ) -> None:
        if not isinstance(registry, ProviderRegistry):
            raise TypeError("registry must be ProviderRegistry")
        self.pool = pool
        self.gateway = gateway
        self.registry = registry

    async def __call__(
        self,
        context: JobContext,
        payload: Mapping[str, object],
    ) -> JobOutcome:
        if not isinstance(context, JobContext):
            raise TypeError("context must be JobContext")
        if context.stop_event.is_set():
            raise asyncio.CancelledError
        if not context.user_uid:
            return JobOutcome.fail(
                "InvalidOperationJob",
                "mail operation job requires a user",
            )
        operation_id = str(payload.get("operation_id") or "").strip()
        if not operation_id:
            return JobOutcome.fail(
                "InvalidOperationJob",
                "mail operation job requires operation_id",
            )
        summary = await self.handle(
            TenantContext(context.user_uid),
            operation_id,
        )
        if summary.outcome == "retry":
            return JobOutcome.retry(
                "MailOperationRetry",
                "mail operation will be retried",
            )
        if summary.outcome == "failed":
            return JobOutcome.fail(
                "MailOperationFailed",
                "mail operation failed permanently",
            )
        return JobOutcome.success()

    async def handle(
        self,
        tenant: TenantContext,
        operation_id: str,
        *,
        now: float | None = None,
    ) -> OperationApplySummary:
        timestamp = float(time.time() if now is None else now)
        operation = await self._begin(tenant, operation_id, timestamp)
        terminal_outcomes = {
            "cancelled": "superseded",
            "synced": "superseded",
            "failed": "failed",
            "conflict": "conflict",
            "review_required": "conflict",
        }
        if operation.status in terminal_outcomes:
            return OperationApplySummary(
                operation.id,
                terminal_outcomes[operation.status],
            )

        try:
            remote = await self.gateway.observe(operation)
        except RetryableError:
            return await self._finish_remote_error(
                tenant,
                operation,
                error_class="RetryableRemoteError",
                now=timestamp,
            )
        except PermanentError:
            return await self._finish_remote_error(
                tenant,
                operation,
                error_class="PermanentRemoteError",
                permanent=True,
                now=timestamp,
            )
        except Exception:
            return await self._finish_remote_error(
                tenant,
                operation,
                error_class="UnexpectedRemoteError",
                now=timestamp,
            )

        if remote is None:
            await self._finish(
                tenant,
                operation,
                status="synced",
                outcome="terminal_missing",
                error_class="RemoteMissing",
                error_message="remote message no longer exists",
                remote_version=None,
                mark_missing=True,
                now=timestamp,
            )
            return OperationApplySummary(operation.id, "terminal_missing")

        stale = bool(
            operation.observed_remote_version
            and remote.remote_version != operation.observed_remote_version
        )
        desired = dict(operation.desired_state)
        if stale and operation.kind in MOTION_KINDS:
            if self._motion_already_applied(operation, remote):
                await self._finish(
                    tenant,
                    operation,
                    status="synced",
                    outcome="superseded",
                    error_class="RemoteAlreadyApplied",
                    error_message="desired remote state already exists",
                    remote_version=remote.remote_version,
                    mark_missing=False,
                    now=timestamp,
                )
                return OperationApplySummary(operation.id, "superseded")
            await self._finish(
                tenant,
                operation,
                status="conflict",
                outcome="conflict",
                error_class="StaleRemoteVersion",
                error_message="remote state changed before operation apply",
                remote_version=remote.remote_version,
                mark_missing=False,
                now=timestamp,
            )
            return OperationApplySummary(operation.id, "conflict")

        command = RemoteOperationCommand(
            operation_id=operation.id,
            remote_instance_id=operation.remote_instance_id,
            account_id=operation.account_id,
            provider_key=await self._provider_key(tenant, operation.account_id),
            kind=operation.kind,
            expected_remote_version=remote.remote_version,
            idempotency_key=operation.idempotency_key,
            desired_value=(
                bool(desired.get("value"))
                if operation.kind in {OperationKind.SET_READ, OperationKind.SET_STARRED}
                else None
            ),
            target_native_key=str(desired.get("target_native_key") or ""),
            remote_action=str(desired.get("remote_action") or operation.kind.value),
            allow_copy_delete=bool(desired.get("allow_copy_delete")),
        )
        try:
            result = await self.gateway.apply(command)
        except RetryableError:
            return await self._finish_remote_error(
                tenant,
                operation,
                error_class="RetryableRemoteError",
                now=timestamp,
            )
        except PermanentError:
            return await self._finish_remote_error(
                tenant,
                operation,
                error_class="PermanentRemoteError",
                permanent=True,
                now=timestamp,
            )
        except Exception:
            return await self._finish_remote_error(
                tenant,
                operation,
                error_class="UnexpectedRemoteError",
                now=timestamp,
            )

        outcome = (
            "merged"
            if operation.kind in {
                OperationKind.SET_READ,
                OperationKind.SET_STARRED,
                OperationKind.ADD_LABEL,
                OperationKind.REMOVE_LABEL,
            }
            else "applied"
        )
        await self._finish(
            tenant,
            operation,
            status="synced",
            outcome=outcome,
            error_class="",
            error_message="",
            remote_version=result.remote_version,
            mark_missing=False,
            now=timestamp,
        )
        return OperationApplySummary(operation.id, outcome)

    async def _finish_remote_error(
        self,
        tenant: TenantContext,
        operation: OperationRecord,
        *,
        error_class: str,
        now: float,
        permanent: bool = False,
    ) -> OperationApplySummary:
        status = "failed" if permanent else "retry_wait"
        outcome = "failed" if permanent else "retry"
        await self._finish(
            tenant,
            operation,
            status=status,
            outcome=outcome,
            error_class=error_class,
            error_message=(
                "remote operation cannot be applied"
                if permanent
                else "remote operation will be retried"
            ),
            remote_version=None,
            mark_missing=False,
            now=now,
        )
        return OperationApplySummary(operation.id, outcome)

    async def _begin(
        self,
        tenant: TenantContext,
        operation_id: str,
        now: float,
    ) -> OperationRecord:
        async with SqlUnitOfWork(self.pool) as uow:
            connection = OperationService._connection(uow)
            repository = OperationRepository(connection)
            operation = await repository.mark_applying(
                tenant,
                operation_id,
                now=now,
            )
            if operation is None:
                current = await repository.get(
                    tenant,
                    operation_id,
                    for_update=True,
                )
                if current is not None and current.status in {
                    "cancelled",
                    "synced",
                    "failed",
                    "conflict",
                    "review_required",
                }:
                    await uow.commit()
                    return current
                raise ConflictError("operation is not pending or retryable")
            await uow.commit()
            return operation

    async def _finish(
        self,
        tenant: TenantContext,
        operation: OperationRecord,
        *,
        status: str,
        outcome: str,
        error_class: str,
        error_message: str,
        remote_version: str | None,
        mark_missing: bool,
        now: float,
    ) -> None:
        async with SqlUnitOfWork(self.pool) as uow:
            connection = OperationService._connection(uow)
            current = await OperationRepository(connection).get(
                tenant,
                operation.id,
                for_update=True,
            )
            if current is None or current.status != "applying":
                raise ConflictError("operation is no longer applying")
            row = await fetch_one(
                connection,
                """
                SELECT message.thread_id
                FROM message_remote_instances remote
                JOIN messages message
                  ON message.id = remote.message_id
                 AND message.user_uid = remote.user_uid
                WHERE remote.user_uid = %s AND remote.id = %s
                FOR UPDATE
                """,
                (tenant.user_uid, operation.remote_instance_id),
            )
            if row is None:
                raise NotFoundError("operation remote instance was not found")
            thread_id = str(row["thread_id"])
            async with connection.cursor() as cursor:
                if remote_version is not None:
                    await cursor.execute(
                        """
                        UPDATE message_remote_instances
                        SET remote_version = %s, updated_at = %s
                        WHERE user_uid = %s AND id = %s
                        """,
                        (remote_version, now, tenant.user_uid, operation.remote_instance_id),
                    )
                if mark_missing:
                    await cursor.execute(
                        "DELETE FROM message_memberships WHERE user_uid = %s AND remote_instance_id = %s",
                        (tenant.user_uid, operation.remote_instance_id),
                    )
                    await cursor.execute(
                        """
                        UPDATE message_remote_instances
                        SET remote_deleted = 1, updated_at = %s
                        WHERE user_uid = %s AND id = %s
                        """,
                        (now, tenant.user_uid, operation.remote_instance_id),
                    )
            await OperationRepository(connection).finish(
                tenant,
                operation.id,
                status=status,
                error_class=error_class,
                error_message=error_message,
                now=now,
                completed=status in {"synced", "failed", "cancelled"},
            )
            await ThreadRepository(connection).refresh_projections(
                tenant,
                (thread_id,),
                now=now,
            )
            await OutboxRepository(connection, tenant).append(
                f"mail.operation.{outcome}",
                operation.id,
                {
                    "operation_id": operation.id,
                    "outcome": outcome,
                    "thread_id": thread_id,
                },
                aggregate_type="mail_operation",
                now=now,
            )
            await uow.commit()

    async def _provider_key(self, tenant: TenantContext, account_id: str) -> str:
        async with self.pool.acquire() as connection:
            row = await fetch_one(
                connection,
                """
                SELECT provider_key
                FROM mail_accounts
                WHERE user_uid = %s AND id = %s AND status = 'active'
                """,
                (tenant.user_uid, account_id),
            )
        if row is None:
            raise NotFoundError("operation account was not found")
        provider_key = str(row["provider_key"])
        self.registry.get(provider_key)
        return provider_key

    @staticmethod
    def _motion_already_applied(
        operation: OperationRecord,
        remote: RemoteOperationState,
    ) -> bool:
        desired = operation.desired_state
        action = str(desired.get("remote_action") or "")
        target = str(desired.get("target_native_key") or "")
        if action == "remove_label":
            return bool(target and target not in remote.mailbox_native_keys)
        if action == "move":
            return bool(target and target in remote.mailbox_native_keys)
        return False
