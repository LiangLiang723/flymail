"""HTTP-facing orchestration for local-first mail operations."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

import aiomysql

from flymail.application.auth import AuthenticatedSession
from flymail.domain.errors import ApiContractError, ConflictError, NotFoundError
from flymail.domain.ids import new_id
from flymail.domain.operations import OperationKind
from flymail.infrastructure.db.pool import DatabasePool
from flymail.providers.registry import ProviderRegistry
from flymail.repositories.base import TenantContext
from flymail.repositories.jobs import JobRepository, JobSpec
from flymail.repositories.outbox import OutboxRepository
from flymail.workers.operation_apply import OperationService


@dataclass(frozen=True, slots=True)
class AcceptedOperations:
    operation_group_id: str | None
    operation_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class UndoResult:
    operation_id: str
    status: str


@dataclass(frozen=True, slots=True)
class PermanentDeleteConfirmation:
    token: str
    expires_at: float


@dataclass(frozen=True, slots=True)
class BulkOperationAccepted:
    bulk_operation_id: str
    job_id: str


class MailOperationApiService:
    """Validate API commands and delegate transactional work to OperationService."""

    def __init__(
        self,
        pool: DatabasePool,
        session_secret: str,
        registry: ProviderRegistry | None = None,
        *,
        now_fn=time.time,
    ) -> None:
        if not isinstance(pool, DatabasePool):
            raise TypeError("pool must be DatabasePool")
        secret = str(session_secret or "")
        if len(secret) < 16:
            raise ValueError("session_secret must be at least 16 characters")
        self.pool = pool
        self.core = OperationService(pool, registry or ProviderRegistry.default())
        self.confirmation_key = hmac.new(
            secret.encode("utf-8"),
            b"flymail-v2/permanent-delete-confirmation/v1",
            hashlib.sha256,
        ).digest()
        self.now_fn = now_fn

    @staticmethod
    def _kind(value: str) -> OperationKind:
        try:
            return OperationKind(str(value or ""))
        except ValueError:
            raise ApiContractError(
                "validation_error",
                "邮件操作类型无效",
                status_code=422,
            ) from None

    @staticmethod
    def _validate_state(kind: OperationKind, value: dict) -> dict[str, object]:
        state = dict(value or {})
        if kind in {OperationKind.SET_READ, OperationKind.SET_STARRED}:
            if not isinstance(state.get("value"), bool):
                raise ApiContractError(
                    "validation_error",
                    "该操作需要布尔状态",
                    status_code=422,
                )
        elif kind in {
            OperationKind.ADD_LABEL,
            OperationKind.REMOVE_LABEL,
            OperationKind.MOVE,
        }:
            mailbox_id = str(state.get("mailbox_id") or "").strip()
            if not mailbox_id:
                raise ApiContractError(
                    "validation_error",
                    "该操作需要目标文件夹或标签",
                    status_code=422,
                )
            state = {"mailbox_id": mailbox_id}
        elif state:
            raise ApiContractError(
                "validation_error",
                "该操作不接受额外状态",
                status_code=422,
            )
        return state

    @staticmethod
    def _b64encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")

    @staticmethod
    def _b64decode(value: str) -> bytes:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

    async def _delete_snapshot(
        self,
        tenant: TenantContext,
        target_type: str,
        target_id: str,
    ) -> str:
        normalized_type = str(target_type or "").strip()
        normalized_id = str(target_id or "").strip()
        if normalized_type not in {"remote_instance", "thread"}:
            raise ApiContractError(
                "validation_error",
                "永久删除目标无效",
                status_code=422,
            )
        target_clause = "ri.id = %s" if normalized_type == "remote_instance" else "m.thread_id = %s"
        async with self.pool.acquire() as connection:
            async with connection.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(
                    f"""
                    SELECT ri.id, ri.remote_version, ri.remote_deleted,
                           EXISTS (
                               SELECT 1
                               FROM message_memberships trash_membership
                               JOIN mailboxes trash_mailbox
                                 ON trash_mailbox.id = trash_membership.mailbox_id
                                AND trash_mailbox.user_uid = trash_membership.user_uid
                               WHERE trash_membership.user_uid = ri.user_uid
                                 AND trash_membership.remote_instance_id = ri.id
                                 AND trash_mailbox.semantic_key = 'trash'
                           ) AS in_trash
                    FROM message_remote_instances ri
                    JOIN messages m
                      ON m.id = ri.message_id AND m.user_uid = ri.user_uid
                    JOIN mail_accounts a
                      ON a.id = ri.account_id AND a.user_uid = ri.user_uid
                     AND a.status = 'active'
                    WHERE ri.user_uid = %s AND {target_clause}
                    ORDER BY ri.id
                    """,
                    (tenant.user_uid, normalized_id),
                )
                rows = [dict(row) for row in await cursor.fetchall()]
        if not rows:
            raise NotFoundError("permanent delete target was not found")
        if any(bool(row["remote_deleted"]) or not bool(row["in_trash"]) for row in rows):
            raise ConflictError("all messages must still be in trash")
        payload = [
            {
                "id": str(row["id"]),
                "remote_version": str(row["remote_version"] or ""),
                "in_trash": True,
            }
            for row in rows
        ]
        return hashlib.sha256(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ).hexdigest()

    async def issue_delete_confirmation(
        self,
        session: AuthenticatedSession,
        *,
        target_type: str,
        target_id: str,
    ) -> PermanentDeleteConfirmation:
        tenant = TenantContext(session.user.id)
        normalized_type = str(target_type or "").strip()
        normalized_id = str(target_id or "").strip()
        snapshot = await self._delete_snapshot(tenant, normalized_type, normalized_id)
        expires_at = float(self.now_fn()) + 300
        payload = json.dumps(
            {
                "v": 1,
                "user_uid": tenant.user_uid,
                "target_type": normalized_type,
                "target_id": normalized_id,
                "snapshot": snapshot,
                "expires_at": expires_at,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        signature = hmac.new(self.confirmation_key, payload, hashlib.sha256).digest()
        return PermanentDeleteConfirmation(
            token=f"{self._b64encode(payload)}.{self._b64encode(signature)}",
            expires_at=expires_at,
        )

    async def _verify_delete_confirmation(
        self,
        tenant: TenantContext,
        *,
        target_type: str,
        target_id: str,
        token: str,
    ) -> None:
        try:
            encoded_payload, encoded_signature = str(token or "").split(".", 1)
            payload = self._b64decode(encoded_payload)
            signature = self._b64decode(encoded_signature)
            expected = hmac.new(self.confirmation_key, payload, hashlib.sha256).digest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError
            decoded = json.loads(payload.decode("utf-8"))
            if not isinstance(decoded, dict) or decoded.get("v") != 1:
                raise ValueError
            if decoded.get("user_uid") != tenant.user_uid:
                raise ValueError
            if decoded.get("target_type") != target_type or decoded.get("target_id") != target_id:
                raise ValueError
            if float(decoded.get("expires_at") or 0) < float(self.now_fn()):
                raise ValueError
            try:
                current = await self._delete_snapshot(tenant, target_type, target_id)
            except (ConflictError, NotFoundError):
                raise ValueError from None
            if not hmac.compare_digest(str(decoded.get("snapshot") or ""), current):
                raise ValueError
        except (ValueError, TypeError, KeyError, json.JSONDecodeError, UnicodeDecodeError):
            raise ApiContractError(
                "invalid_confirmation_token",
                "永久删除确认已失效，请重新确认",
                status_code=409,
            ) from None

    async def create(
        self,
        session: AuthenticatedSession,
        *,
        target_type: str,
        target_id: str,
        operation_type: str,
        desired_state: dict,
        idempotency_key: str,
        confirmation_token: str | None,
    ) -> AcceptedOperations:
        tenant = TenantContext(session.user.id)
        kind = self._kind(operation_type)
        state = self._validate_state(kind, desired_state)
        normalized_type = str(target_type or "").strip()
        normalized_target = str(target_id or "").strip()
        normalized_key = str(idempotency_key or "").strip()
        confirmed = False
        if kind is OperationKind.DELETE_PERMANENT:
            await self._verify_delete_confirmation(
                tenant,
                target_type=normalized_type,
                target_id=normalized_target,
                token=str(confirmation_token or ""),
            )
            confirmed = True
        elif confirmation_token:
            raise ApiContractError(
                "validation_error",
                "该操作不接受永久删除确认令牌",
                status_code=422,
            )
        if normalized_type == "remote_instance":
            operation_id = await self.core.record_local_intent(
                tenant,
                remote_instance_id=normalized_target,
                kind=kind,
                desired_state=state,
                idempotency_key=normalized_key,
                confirm_permanent=confirmed,
            )
            return AcceptedOperations(None, (operation_id,))
        if normalized_type == "thread":
            result = await self.core.record_thread_intent(
                tenant,
                thread_id=normalized_target,
                kind=kind,
                desired_state=state,
                idempotency_key=normalized_key,
                confirm_permanent=confirmed,
            )
            return AcceptedOperations(
                result.operation_group_id,
                result.operation_ids,
            )
        raise ApiContractError(
            "validation_error",
            "邮件操作目标无效",
            status_code=422,
        )

    async def undo(
        self,
        session: AuthenticatedSession,
        operation_id: str,
        *,
        idempotency_key: str,
    ) -> UndoResult:
        tenant = TenantContext(session.user.id)
        result_id = await self.core.undo(
            tenant,
            str(operation_id or "").strip(),
            idempotency_key=str(idempotency_key or "").strip(),
        )
        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT status
                    FROM mail_operations
                    WHERE id = %s AND user_uid = %s
                    """,
                    (result_id, tenant.user_uid),
                )
                row = await cursor.fetchone()
        status = str(row[0]) if row else "pending"
        return UndoResult(
            operation_id=result_id,
            status="cancelled" if status == "cancelled" else "pending",
        )

    async def mark_all_read(
        self,
        session: AuthenticatedSession,
        *,
        semantic_mailbox: str,
        account_id: str | None,
        native_label: str | None,
        idempotency_key: str,
    ) -> BulkOperationAccepted:
        tenant = TenantContext(session.user.id)
        snapshot = {
            "semantic_mailbox": str(semantic_mailbox or "inbox").strip().casefold(),
            "account_id": str(account_id or "").strip() or None,
            "native_label": str(native_label or "").strip() or None,
        }
        if not snapshot["semantic_mailbox"]:
            raise ApiContractError(
                "validation_error",
                "邮箱分类无效",
                status_code=422,
            )
        normalized_key = str(idempotency_key or "").strip()
        timestamp = float(self.now_fn())
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                async with connection.cursor(aiomysql.DictCursor) as cursor:
                    await cursor.execute(
                        """
                        SELECT id, filter_json
                        FROM bulk_mail_operations
                        WHERE user_uid = %s AND idempotency_key = %s
                        FOR UPDATE
                        """,
                        (tenant.user_uid, normalized_key),
                    )
                    existing = await cursor.fetchone()
                    if existing is not None:
                        stored = existing["filter_json"]
                        if isinstance(stored, str):
                            stored = json.loads(stored)
                        if stored != snapshot:
                            raise ConflictError(
                                "idempotency key belongs to another bulk operation"
                            )
                        bulk_id = str(existing["id"])
                    else:
                        bulk_id = new_id("bulkop")
                        await cursor.execute(
                            """
                            INSERT INTO bulk_mail_operations (
                                id, user_uid, operation_type, filter_json,
                                cursor_remote_id, status, matched_count,
                                operation_count, idempotency_key,
                                created_at, updated_at
                            ) VALUES (%s, %s, 'set_read', %s, NULL, 'pending',
                                      0, 0, %s, %s, %s)
                            """,
                            (
                                bulk_id,
                                tenant.user_uid,
                                json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
                                normalized_key,
                                timestamp,
                                timestamp,
                            ),
                        )
                        await OutboxRepository(connection, tenant).append(
                            "mail.bulk_operation.pending",
                            bulk_id,
                            {"bulk_operation_id": bulk_id, "operation_type": "set_read"},
                            aggregate_type="bulk_mail_operation",
                            now=timestamp,
                        )
                job_id = await JobRepository(connection).enqueue(
                    JobSpec(
                        queue_name="operations",
                        job_kind="mail.operation.bulk_mark_read",
                        payload={"bulk_operation_id": bulk_id},
                        user_uid=tenant.user_uid,
                        priority=25,
                        available_at=timestamp,
                        max_attempts=10000,
                        dedupe_key=f"mail-bulk-operation:{bulk_id}",
                    ),
                    now=timestamp,
                )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
        return BulkOperationAccepted(bulk_operation_id=bulk_id, job_id=job_id)
