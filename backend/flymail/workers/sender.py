"""Durable, idempotent SMTP delivery and sent-result verification."""

from __future__ import annotations

import gzip
import json
import time
from dataclasses import dataclass
from typing import Mapping

import aiomysql
from flymail.domain.enums import ObjectKind
from flymail.domain.errors import ConflictError, NotFoundError, PermanentError, RetryableError
from flymail.domain.ids import new_id
from flymail.infrastructure.db.pool import DatabasePool
from flymail.infrastructure.db.uow import SqlUnitOfWork
from flymail.infrastructure.object_store.models import StoredObject
from flymail.infrastructure.object_store.store import ObjectStore
from flymail.providers.contracts import SentCopyStrategy
from flymail.providers.core.smtp_client import (
    ComposedAttachment,
    MimeComposer,
    SendCommand,
    SendRecipient,
    SentAppendRequest,
    SentVerificationRequest,
    SmtpDeliveryUncertain,
    SmtpMailGateway,
    SmtpSendRequest,
    validate_mailbox_address,
)
from flymail.providers.registry import ProviderRegistry
from flymail.repositories.base import TenantContext, fetch_all, fetch_one
from flymail.repositories.jobs import JobRepository, JobSpec
from flymail.repositories.objects import ObjectRepository
from flymail.repositories.outbox import OutboxRepository
from flymail.workers.dispatcher import JobContext, JobOutcome


@dataclass(frozen=True, slots=True)
class QueuedSend:
    draft_id: str
    operation_id: str
    job_id: str
    message_id_header: str


@dataclass(frozen=True, slots=True)
class _DraftScope:
    draft_id: str
    user_uid: str
    account_id: str
    provider_key: str
    identity_id: str
    status: str
    send_state: str
    scheduled_at: float | None
    message_id_header: str
    composed_object_sha256: str
    verification_attempts: int
    created_at: float


@dataclass(frozen=True, slots=True)
class _DeliveryStart:
    draft: _DraftScope
    operation_id: str
    attempt_id: str
    attempt_number: int
    terminal: bool = False
    not_due: bool = False


async def _single_chunk(value: bytes):
    yield value


def _required_text(value: object, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{label} is required")
    return normalized


def _safe_json(value: dict[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class SendService:
    def __init__(
        self,
        pool: DatabasePool,
        store: ObjectStore,
        registry: ProviderRegistry,
    ) -> None:
        if not isinstance(pool, DatabasePool):
            raise TypeError("pool must be DatabasePool")
        if not isinstance(store, ObjectStore):
            raise TypeError("store must be ObjectStore")
        if not isinstance(registry, ProviderRegistry):
            raise TypeError("registry must be ProviderRegistry")
        self.pool = pool
        self.store = store
        self.registry = registry

    async def queue_draft(
        self,
        tenant: TenantContext,
        draft_id: str,
        *,
        idempotency_key: str,
        now: float | None = None,
    ) -> QueuedSend:
        timestamp = float(time.time() if now is None else now)
        normalized_draft = _required_text(draft_id, "draft_id")
        normalized_key = _required_text(idempotency_key, "idempotency_key")
        async with SqlUnitOfWork(self.pool) as uow:
            connection = self._connection(uow)
            await self._lock_user(connection, tenant)
            draft = await self._load_queue_scope(
                connection,
                tenant,
                normalized_draft,
                for_update=True,
            )
            if draft.status in {"sent", "cancelled"}:
                raise ConflictError("draft can no longer be queued")
            existing = await fetch_one(
                connection,
                """
                SELECT id, target_id, account_id, desired_state
                FROM mail_operations
                WHERE user_uid = %s AND idempotency_key = %s
                FOR UPDATE
                """,
                (tenant.user_uid, normalized_key),
            )
            if existing is not None:
                desired = self._decode_json(existing["desired_state"])
                if (
                    str(existing["target_id"]) != normalized_draft
                    or str(existing["account_id"] or "") != draft.account_id
                    or str(desired.get("message_id_header") or "") != draft.message_id_header
                ):
                    raise ConflictError("idempotency key belongs to another send intent")
                job = await fetch_one(
                    connection,
                    """
                    SELECT id FROM worker_jobs
                    WHERE queue_name = 'send'
                      AND dedupe_key = %s
                    LIMIT 1
                    """,
                    (f"send-deliver:{existing['id']}:0",),
                )
                if job is None:
                    raise ConflictError("queued send job is missing")
                await uow.commit()
                return QueuedSend(
                    draft_id=normalized_draft,
                    operation_id=str(existing["id"]),
                    job_id=str(job["id"]),
                    message_id_header=draft.message_id_header,
                )

            if draft.status not in {"draft", "failed", "review_required"}:
                raise ConflictError("draft is already queued or sending")
            message_id_header = draft.message_id_header or self._new_message_id(draft.account_id)
            operation_id = new_id("op")
            desired_state = {
                "draft_id": normalized_draft,
                "message_id_header": message_id_header,
            }
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO mail_operations (
                        id, user_uid, operation_group_id, operation_type,
                        target_type, target_id, account_id, remote_instance_id,
                        desired_state, observed_remote_version, status,
                        priority, available_at, attempt_count,
                        last_error_class, last_error_message,
                        idempotency_key, created_at, updated_at, completed_at
                    ) VALUES (%s, %s, NULL, 'send', 'draft', %s, %s, NULL,
                              %s, '', 'pending', 10, %s, 0, '', '', %s,
                              %s, %s, NULL)
                    """,
                    (
                        operation_id,
                        tenant.user_uid,
                        normalized_draft,
                        draft.account_id,
                        _safe_json(desired_state),
                        max(timestamp, draft.scheduled_at or timestamp),
                        normalized_key,
                        timestamp,
                        timestamp,
                    ),
                )
                await cursor.execute(
                    """
                    UPDATE drafts
                    SET status = 'queued', send_state = 'queued',
                        send_message_id = %s, queued_at = %s,
                        verification_attempts = 0, updated_at = %s
                    WHERE user_uid = %s AND id = %s
                    """,
                    (
                        message_id_header,
                        timestamp,
                        timestamp,
                        tenant.user_uid,
                        normalized_draft,
                    ),
                )
            available_at = max(timestamp, draft.scheduled_at or timestamp)
            job_id = await JobRepository(connection).enqueue(
                JobSpec(
                    queue_name="send",
                    job_kind="send.deliver",
                    payload={
                        "draft_id": normalized_draft,
                        "operation_id": operation_id,
                    },
                    user_uid=tenant.user_uid,
                    account_id=draft.account_id,
                    provider_key=draft.provider_key,
                    priority=10,
                    available_at=available_at,
                    max_attempts=3,
                    dedupe_key=f"send-deliver:{operation_id}:0",
                ),
                now=timestamp,
            )
            await OutboxRepository(connection, tenant).append(
                "mail.send.queued",
                operation_id,
                {
                    "operation_id": operation_id,
                    "draft_id": normalized_draft,
                    "job_id": job_id,
                    "available_at": available_at,
                },
                aggregate_type="mail_operation",
                now=timestamp,
            )
            await uow.commit()
            return QueuedSend(
                draft_id=normalized_draft,
                operation_id=operation_id,
                job_id=job_id,
                message_id_header=message_id_header,
            )

    async def cancel(
        self,
        tenant: TenantContext,
        draft_id: str,
        *,
        operation_id: str,
        now: float | None = None,
    ) -> None:
        timestamp = float(time.time() if now is None else now)
        normalized_draft = _required_text(draft_id, "draft_id")
        normalized_operation = _required_text(operation_id, "operation_id")
        async with SqlUnitOfWork(self.pool) as uow:
            connection = self._connection(uow)
            await self._lock_user(connection, tenant)
            row = await fetch_one(
                connection,
                """
                SELECT d.status, d.send_state, o.status AS operation_status
                FROM drafts d
                JOIN mail_operations o
                  ON o.target_id = d.id AND o.user_uid = d.user_uid
                WHERE d.user_uid = %s AND d.id = %s AND o.id = %s
                FOR UPDATE
                """,
                (tenant.user_uid, normalized_draft, normalized_operation),
            )
            if row is None:
                raise NotFoundError("queued send was not found")
            if str(row["status"]) != "queued" or str(row["operation_status"]) not in {
                "pending",
                "retry_wait",
            }:
                raise ConflictError("send can no longer be cancelled")
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    UPDATE drafts
                    SET status = 'cancelled', send_state = 'cancelled',
                        updated_at = %s
                    WHERE user_uid = %s AND id = %s
                    """,
                    (timestamp, tenant.user_uid, normalized_draft),
                )
                await cursor.execute(
                    """
                    UPDATE mail_operations
                    SET status = 'cancelled', last_error_class = 'UserCancelled',
                        last_error_message = 'cancelled before SMTP delivery',
                        completed_at = %s, updated_at = %s
                    WHERE user_uid = %s AND id = %s
                    """,
                    (
                        timestamp,
                        timestamp,
                        tenant.user_uid,
                        normalized_operation,
                    ),
                )
                await cursor.execute(
                    """
                    UPDATE worker_jobs
                    SET status = 'cancelled', finished_at = %s, updated_at = %s
                    WHERE user_uid = %s
                      AND JSON_UNQUOTE(JSON_EXTRACT(payload, '$.operation_id')) = %s
                      AND status IN ('pending', 'retry_wait')
                    """,
                    (
                        timestamp,
                        timestamp,
                        tenant.user_uid,
                        normalized_operation,
                    ),
                )
            await OutboxRepository(connection, tenant).append(
                "mail.send.cancelled",
                normalized_operation,
                {
                    "operation_id": normalized_operation,
                    "draft_id": normalized_draft,
                },
                aggregate_type="mail_operation",
                now=timestamp,
            )
            await uow.commit()

    async def _load_queue_scope(
        self,
        connection: aiomysql.Connection,
        tenant: TenantContext,
        draft_id: str,
        *,
        for_update: bool,
    ) -> _DraftScope:
        suffix = " FOR UPDATE" if for_update else ""
        row = await fetch_one(
            connection,
            """
            SELECT d.id, d.user_uid, d.account_id, d.identity_id,
                   d.status, d.send_state, d.scheduled_at,
                   d.send_message_id, d.composed_object_sha256,
                   d.verification_attempts, d.created_at,
                   a.provider_key, a.status AS account_status,
                   i.account_id AS identity_account_id,
                   i.is_verified, i.from_address, i.reply_to
            FROM drafts d
            JOIN mail_accounts a
              ON a.id = d.account_id AND a.user_uid = d.user_uid
            JOIN mail_identities i
              ON i.id = d.identity_id AND i.user_uid = d.user_uid
            WHERE d.user_uid = %s AND d.id = %s
            """ + suffix,
            (tenant.user_uid, draft_id),
        )
        if row is None:
            raise NotFoundError("draft was not found")
        if str(row["account_status"]) != "active":
            raise ConflictError("mail account is not active")
        if str(row["identity_account_id"]) != str(row["account_id"]):
            raise ConflictError("sender identity belongs to another account")
        if not bool(row["is_verified"]):
            raise ConflictError("sender identity is not verified")
        validate_mailbox_address(str(row["from_address"]), "from_address")
        reply_to = str(row["reply_to"] or "").strip()
        if reply_to:
            validate_mailbox_address(reply_to, "reply_to")
        recipient_rows = await fetch_all(
            connection,
            """
            SELECT recipient_kind, address, display_name
            FROM draft_recipients
            WHERE user_uid = %s AND draft_id = %s
            ORDER BY FIELD(recipient_kind, 'to', 'cc', 'bcc'), position_index, id
            """,
            (tenant.user_uid, draft_id),
        )
        recipients = tuple(
            SendRecipient(
                str(item["recipient_kind"]),
                str(item["address"]),
                str(item["display_name"] or ""),
            )
            for item in recipient_rows
        )
        if not any(recipient.kind == "to" for recipient in recipients):
            raise ValueError("at least one To recipient is required")
        normalized_addresses = [recipient.address.casefold() for recipient in recipients]
        if len(normalized_addresses) != len(set(normalized_addresses)):
            raise ValueError("recipient addresses must be unique")
        self.registry.get(str(row["provider_key"]))
        return _DraftScope(
            draft_id=str(row["id"]),
            user_uid=str(row["user_uid"]),
            account_id=str(row["account_id"]),
            provider_key=str(row["provider_key"]),
            identity_id=str(row["identity_id"]),
            status=str(row["status"]),
            send_state=str(row["send_state"]),
            scheduled_at=float(row["scheduled_at"]) if row["scheduled_at"] is not None else None,
            message_id_header=str(row["send_message_id"] or ""),
            composed_object_sha256=str(row["composed_object_sha256"] or ""),
            verification_attempts=int(row["verification_attempts"] or 0),
            created_at=float(row["created_at"] or 0),
        )

    @staticmethod
    async def _lock_user(connection: aiomysql.Connection, tenant: TenantContext) -> None:
        row = await fetch_one(
            connection,
            "SELECT id FROM users WHERE id = %s AND enabled = 1 FOR UPDATE",
            (tenant.user_uid,),
        )
        if row is None:
            raise NotFoundError("user was not found")

    @staticmethod
    def _new_message_id(account_id: str) -> str:
        return f"<{new_id('send')}@{_required_text(account_id, 'account_id')}.flymail>"

    @staticmethod
    def _decode_json(value: object) -> dict[str, object]:
        if isinstance(value, dict):
            return dict(value)
        if not value:
            return {}
        decoded = json.loads(str(value))
        return dict(decoded) if isinstance(decoded, dict) else {}

    @staticmethod
    def _connection(uow: SqlUnitOfWork) -> aiomysql.Connection:
        if uow.connection is None:
            raise RuntimeError("unit of work connection is unavailable")
        return uow.connection


class ReliableSender:
    def __init__(
        self,
        pool: DatabasePool,
        store: ObjectStore,
        gateway: SmtpMailGateway,
        registry: ProviderRegistry,
        *,
        verification_retry_limit: int = 1,
    ) -> None:
        if not isinstance(pool, DatabasePool):
            raise TypeError("pool must be DatabasePool")
        if not isinstance(store, ObjectStore):
            raise TypeError("store must be ObjectStore")
        if not isinstance(registry, ProviderRegistry):
            raise TypeError("registry must be ProviderRegistry")
        if isinstance(verification_retry_limit, bool) or int(verification_retry_limit) < 0:
            raise ValueError("verification_retry_limit must be non-negative")
        self.pool = pool
        self.store = store
        self.gateway = gateway
        self.registry = registry
        self.verification_retry_limit = int(verification_retry_limit)

    async def handle(
        self,
        context: JobContext,
        payload: Mapping[str, object],
    ) -> JobOutcome:
        return await self.deliver(context, payload)

    async def deliver(
        self,
        context: JobContext,
        payload: Mapping[str, object],
    ) -> JobOutcome:
        tenant, draft_id, operation_id = self._job_scope(context, payload)
        timestamp = time.time()
        start = await self._begin_delivery(
            tenant,
            draft_id,
            operation_id,
            expected_account_id=_required_text(context.account_id, "account_id"),
            expected_provider_key=_required_text(context.provider_key, "provider_key"),
            now=timestamp,
        )
        if start.terminal:
            return JobOutcome.success()
        if start.not_due:
            return JobOutcome.retry(
                "SendNotDue",
                "scheduled send is not due",
                base_seconds=30,
                max_seconds=300,
            )
        try:
            _, composed = await self._load_and_compose(
                tenant,
                start.draft,
                now=timestamp,
            )
        except (PermanentError, ValueError, UnicodeError):
            await self._mark_delivery_failure(
                tenant,
                start,
                permanent=True,
                now=timestamp,
            )
            return JobOutcome.fail(
                "InvalidSendContent",
                "message content cannot be composed",
            )

        plugin = self.registry.get(start.draft.provider_key)
        if composed.requires_smtp_utf8 and not plugin.capabilities().supports_smtp_utf8:
            await self._mark_delivery_failure(
                tenant,
                start,
                permanent=True,
                now=timestamp,
            )
            return JobOutcome.fail(
                "SmtpUtf8Unsupported",
                "provider does not support the required SMTPUTF8 envelope",
            )
        request = SmtpSendRequest(
            account_id=start.draft.account_id,
            message_id_header=composed.message_id_header,
            envelope_from=composed.envelope_from,
            envelope_recipients=composed.envelope_recipients,
            source=composed.source,
            use_smtp_utf8=composed.requires_smtp_utf8,
        )
        try:
            result = await self.gateway.send(request)
        except SmtpDeliveryUncertain:
            await self._mark_verification_required(
                tenant,
                start,
                now=timestamp,
            )
            return JobOutcome.success()
        except PermanentError:
            await self._mark_delivery_failure(
                tenant,
                start,
                permanent=True,
                now=timestamp,
            )
            return JobOutcome.fail(
                "PermanentSmtpFailure",
                "message cannot be delivered",
            )
        except RetryableError:
            await self._mark_delivery_failure(
                tenant,
                start,
                permanent=False,
                now=timestamp,
            )
            return JobOutcome.retry(
                "RetryableSmtpFailure",
                "message delivery will be retried",
            )
        except Exception:
            await self._mark_delivery_failure(
                tenant,
                start,
                permanent=False,
                now=timestamp,
            )
            return JobOutcome.retry(
                "UnexpectedSmtpFailure",
                "message delivery will be retried",
            )
        await self._mark_sent(
            tenant,
            start,
            response_code=result.response_code,
            safe_response=result.safe_response,
            enqueue_append=True,
            now=timestamp,
        )
        return JobOutcome.success()

    async def verify(
        self,
        context: JobContext,
        payload: Mapping[str, object],
    ) -> JobOutcome:
        tenant, draft_id, operation_id = self._job_scope(context, payload)
        timestamp = time.time()
        async with self.pool.acquire() as connection:
            row = await fetch_one(
                connection,
                """
                SELECT d.id, d.account_id, d.send_message_id,
                       d.send_state, d.verification_attempts,
                       a.provider_key,
                       MIN(sa.started_at) AS first_started_at
                FROM drafts d
                JOIN mail_accounts a
                  ON a.id = d.account_id AND a.user_uid = d.user_uid
                JOIN mail_operations o
                  ON o.target_id = d.id AND o.user_uid = d.user_uid
                LEFT JOIN send_attempts sa
                  ON sa.operation_id = o.id AND sa.user_uid = o.user_uid
                WHERE d.user_uid = %s AND d.id = %s AND o.id = %s
                GROUP BY d.id, d.account_id, d.send_message_id,
                         d.send_state, d.verification_attempts,
                         a.provider_key
                """,
                (tenant.user_uid, draft_id, operation_id),
            )
            recipient_rows = await fetch_all(
                connection,
                """
                SELECT address FROM draft_recipients
                WHERE user_uid = %s AND draft_id = %s
                ORDER BY FIELD(recipient_kind, 'to', 'cc', 'bcc'), position_index, id
                """,
                (tenant.user_uid, draft_id),
            )
        if row is None:
            raise NotFoundError("send verification target was not found")
        if (
            str(row["account_id"]) != _required_text(context.account_id, "account_id")
            or str(row["provider_key"])
            != _required_text(context.provider_key, "provider_key")
        ):
            raise ConflictError("verification job scope does not match persisted draft")
        if str(row["send_state"]) == "sent":
            return JobOutcome.success()
        if str(row["send_state"]) != "verification_required":
            raise ConflictError("send is not awaiting verification")
        verification = await self.gateway.verify_sent(
            SentVerificationRequest(
                account_id=str(row["account_id"]),
                message_id_header=str(row["send_message_id"]),
                started_at=float(row["first_started_at"] or 0),
                recipients=tuple(str(item["address"]) for item in recipient_rows),
            )
        )
        if verification.found:
            start = _DeliveryStart(
                draft=_DraftScope(
                    draft_id=draft_id,
                    user_uid=tenant.user_uid,
                    account_id=str(row["account_id"]),
                    provider_key=_required_text(context.provider_key, "provider_key"),
                    identity_id="verified",
                    status="review_required",
                    send_state="verification_required",
                    scheduled_at=None,
                    message_id_header=str(row["send_message_id"]),
                    composed_object_sha256="",
                    verification_attempts=int(row["verification_attempts"] or 0),
                    created_at=0,
                ),
                operation_id=operation_id,
                attempt_id="",
                attempt_number=0,
            )
            await self._mark_sent(
                tenant,
                start,
                response_code=None,
                safe_response="verified in Sent mailbox",
                enqueue_append=False,
                now=timestamp,
            )
            return JobOutcome.success()
        await self._handle_verification_miss(
            tenant,
            draft_id,
            operation_id,
            context,
            now=timestamp,
        )
        return JobOutcome.success()

    async def append_sent_copy(
        self,
        context: JobContext,
        payload: Mapping[str, object],
    ) -> JobOutcome:
        tenant, draft_id, operation_id = self._job_scope(context, payload)
        expected_account_id = _required_text(context.account_id, "account_id")
        expected_provider_key = _required_text(context.provider_key, "provider_key")
        timestamp = time.time()
        async with SqlUnitOfWork(self.pool) as uow:
            connection = self._connection(uow)
            row = await fetch_one(
                connection,
                """
                SELECT d.account_id, d.send_message_id,
                       d.composed_object_sha256, d.status, d.send_state,
                       a.provider_key
                FROM drafts d
                JOIN mail_accounts a
                  ON a.id = d.account_id AND a.user_uid = d.user_uid
                JOIN mail_operations o
                  ON o.target_id = d.id AND o.user_uid = d.user_uid
                WHERE d.user_uid = %s AND d.id = %s AND o.id = %s
                  AND o.operation_type = 'send'
                FOR UPDATE
                """,
                (tenant.user_uid, draft_id, operation_id),
            )
            if row is None:
                raise NotFoundError("sent draft was not found")
            account_id = str(row["account_id"])
            provider_key = str(row["provider_key"])
            if account_id != expected_account_id or provider_key != expected_provider_key:
                raise ConflictError("sent-copy job scope does not match persisted draft")
            plugin = self.registry.get(provider_key)
            if plugin.sent_copy_strategy() is SentCopyStrategy.PROVIDER_AUTO:
                await uow.commit()
                return JobOutcome.success()
            appended = await fetch_one(
                connection,
                """
                SELECT id FROM outbox_events
                WHERE user_uid = %s AND aggregate_type = 'draft'
                  AND aggregate_id = %s
                  AND event_type = 'mail.sent_copy.appended'
                LIMIT 1
                """,
                (tenant.user_uid, draft_id),
            )
            if appended is not None:
                await uow.commit()
                return JobOutcome.success()
            started = await fetch_one(
                connection,
                """
                SELECT id FROM outbox_events
                WHERE user_uid = %s AND aggregate_type = 'draft'
                  AND aggregate_id = %s
                  AND event_type = 'mail.sent_copy.append_started'
                LIMIT 1
                """,
                (tenant.user_uid, draft_id),
            )
            if started is not None:
                await self._append_sent_copy_review_event(
                    connection,
                    tenant,
                    draft_id,
                    operation_id,
                    now=timestamp,
                )
                await uow.commit()
                return JobOutcome.fail(
                    "SentCopyResultUncertain",
                    "sent copy may already exist; manual review is required",
                )
            if str(row["status"]) != "sent" or str(row["send_state"]) != "sent":
                raise ConflictError("draft is not sent")
            digest = _required_text(
                row["composed_object_sha256"],
                "composed_object_sha256",
            )
            await OutboxRepository(connection, tenant).append(
                "mail.sent_copy.append_started",
                draft_id,
                {
                    "draft_id": draft_id,
                    "operation_id": operation_id,
                },
                aggregate_type="draft",
                now=timestamp,
            )
            await uow.commit()

        source = await self._read_object(tenant, digest)
        try:
            append_result = await self.gateway.append_sent_copy(
                SentAppendRequest(
                    account_id=account_id,
                    message_id_header=str(row["send_message_id"]),
                    source=source,
                )
            )
        except Exception:
            async with SqlUnitOfWork(self.pool) as uow:
                connection = self._connection(uow)
                await self._append_sent_copy_review_event(
                    connection,
                    tenant,
                    draft_id,
                    operation_id,
                    now=time.time(),
                )
                await uow.commit()
            return JobOutcome.fail(
                "SentCopyResultUncertain",
                "sent copy may already exist; manual review is required",
            )

        async with SqlUnitOfWork(self.pool) as uow:
            connection = self._connection(uow)
            duplicate = await fetch_one(
                connection,
                """
                SELECT id FROM outbox_events
                WHERE user_uid = %s AND aggregate_type = 'draft'
                  AND aggregate_id = %s
                  AND event_type = 'mail.sent_copy.appended'
                LIMIT 1
                """,
                (tenant.user_uid, draft_id),
            )
            if duplicate is None:
                await OutboxRepository(connection, tenant).append(
                    "mail.sent_copy.appended",
                    draft_id,
                    {
                        "draft_id": draft_id,
                        "operation_id": operation_id,
                        "remote_uid": append_result.remote_uid,
                    },
                    aggregate_type="draft",
                    now=time.time(),
                )
            await uow.commit()
        return JobOutcome.success()

    async def _append_sent_copy_review_event(
        self,
        connection: aiomysql.Connection,
        tenant: TenantContext,
        draft_id: str,
        operation_id: str,
        *,
        now: float,
    ) -> None:
        existing = await fetch_one(
            connection,
            """
            SELECT id FROM outbox_events
            WHERE user_uid = %s AND aggregate_type = 'draft'
              AND aggregate_id = %s
              AND event_type = 'mail.sent_copy.review_required'
            LIMIT 1
            """,
            (tenant.user_uid, draft_id),
        )
        if existing is None:
            await OutboxRepository(connection, tenant).append(
                "mail.sent_copy.review_required",
                draft_id,
                {
                    "draft_id": draft_id,
                    "operation_id": operation_id,
                    "reason": "append_result_uncertain",
                },
                aggregate_type="draft",
                now=now,
            )

    async def _begin_delivery(
        self,
        tenant: TenantContext,
        draft_id: str,
        operation_id: str,
        *,
        expected_account_id: str,
        expected_provider_key: str,
        now: float,
    ) -> _DeliveryStart:
        async with SqlUnitOfWork(self.pool) as uow:
            connection = self._connection(uow)
            row = await fetch_one(
                connection,
                """
                SELECT d.id, d.user_uid, d.account_id, d.identity_id,
                       d.status, d.send_state, d.scheduled_at,
                       d.send_message_id, d.composed_object_sha256,
                       d.verification_attempts, d.created_at,
                       a.provider_key, o.status AS operation_status
                FROM drafts d
                JOIN mail_accounts a
                  ON a.id = d.account_id AND a.user_uid = d.user_uid
                JOIN mail_operations o
                  ON o.target_id = d.id AND o.user_uid = d.user_uid
                WHERE d.user_uid = %s AND d.id = %s AND o.id = %s
                FOR UPDATE
                """,
                (tenant.user_uid, draft_id, operation_id),
            )
            if row is None:
                raise NotFoundError("queued send was not found")
            if (
                str(row["account_id"]) != expected_account_id
                or str(row["provider_key"]) != expected_provider_key
            ):
                raise ConflictError("send job scope does not match persisted draft")
            draft = _DraftScope(
                draft_id=str(row["id"]),
                user_uid=str(row["user_uid"]),
                account_id=str(row["account_id"]),
                provider_key=str(row["provider_key"]),
                identity_id=str(row["identity_id"]),
                status=str(row["status"]),
                send_state=str(row["send_state"]),
                scheduled_at=float(row["scheduled_at"]) if row["scheduled_at"] is not None else None,
                message_id_header=str(row["send_message_id"] or ""),
                composed_object_sha256=str(row["composed_object_sha256"] or ""),
                verification_attempts=int(row["verification_attempts"] or 0),
                created_at=float(row["created_at"] or 0),
            )
            if draft.status in {"sent", "cancelled"} or str(row["operation_status"]) in {
                "synced",
                "cancelled",
            }:
                await uow.commit()
                return _DeliveryStart(
                    draft=draft,
                    operation_id=operation_id,
                    attempt_id="",
                    attempt_number=0,
                    terminal=True,
                )
            if draft.scheduled_at is not None and draft.scheduled_at > now:
                await uow.commit()
                return _DeliveryStart(
                    draft=draft,
                    operation_id=operation_id,
                    attempt_id="",
                    attempt_number=0,
                    not_due=True,
                )
            if draft.send_state == "sending":
                attempt = await fetch_one(
                    connection,
                    """
                    SELECT id, attempt_number
                    FROM send_attempts
                    WHERE user_uid = %s AND operation_id = %s
                      AND status = 'sending'
                    ORDER BY attempt_number DESC
                    LIMIT 1 FOR UPDATE
                    """,
                    (tenant.user_uid, operation_id),
                )
                if attempt is None:
                    raise ConflictError("sending draft has no active attempt")
                attempt_id = str(attempt["id"])
                attempt_number = int(attempt["attempt_number"] or 0)
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        UPDATE send_attempts
                        SET status = 'verification_required',
                            safe_response = 'worker stopped while SMTP result was unknown',
                            finished_at = %s
                        WHERE user_uid = %s AND id = %s AND status = 'sending'
                        """,
                        (now, tenant.user_uid, attempt_id),
                    )
                    await cursor.execute(
                        """
                        UPDATE drafts
                        SET status = 'review_required',
                            send_state = 'verification_required', updated_at = %s
                        WHERE user_uid = %s AND id = %s
                        """,
                        (now, tenant.user_uid, draft_id),
                    )
                    await cursor.execute(
                        """
                        UPDATE mail_operations
                        SET status = 'review_required',
                            last_error_class = 'WorkerStoppedDuringSend',
                            last_error_message = 'SMTP result requires verification',
                            updated_at = %s
                        WHERE user_uid = %s AND id = %s
                        """,
                        (now, tenant.user_uid, operation_id),
                    )
                await JobRepository(connection).enqueue(
                    JobSpec(
                        queue_name="send",
                        job_kind="send.verify",
                        payload={"draft_id": draft_id, "operation_id": operation_id},
                        user_uid=tenant.user_uid,
                        account_id=draft.account_id,
                        provider_key=draft.provider_key,
                        priority=5,
                        available_at=now,
                        max_attempts=3,
                        dedupe_key=f"send-verify:{operation_id}:{attempt_number}",
                    ),
                    now=now,
                )
                await OutboxRepository(connection, tenant).append(
                    "mail.send.verification_required",
                    operation_id,
                    {
                        "operation_id": operation_id,
                        "draft_id": draft_id,
                        "reason": "worker_recovery",
                    },
                    aggregate_type="mail_operation",
                    now=now,
                )
                await uow.commit()
                return _DeliveryStart(
                    draft=draft,
                    operation_id=operation_id,
                    attempt_id=attempt_id,
                    attempt_number=attempt_number,
                    terminal=True,
                )
            if draft.send_state not in {"queued", "failed"}:
                raise ConflictError("send is not ready for delivery")
            attempt_row = await fetch_one(
                connection,
                """
                SELECT COALESCE(MAX(attempt_number), 0) AS attempt_number
                FROM send_attempts
                WHERE user_uid = %s AND operation_id = %s
                FOR UPDATE
                """,
                (tenant.user_uid, operation_id),
            )
            attempt_number = int(attempt_row["attempt_number"] or 0) + 1
            attempt_id = new_id("sendatt")
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO send_attempts (
                        id, user_uid, draft_id, operation_id, account_id,
                        message_id_header, attempt_number, status,
                        smtp_response_code, safe_response, started_at,
                        finished_at, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'sending',
                              NULL, '', %s, NULL, %s)
                    """,
                    (
                        attempt_id,
                        tenant.user_uid,
                        draft_id,
                        operation_id,
                        draft.account_id,
                        draft.message_id_header,
                        attempt_number,
                        now,
                        now,
                    ),
                )
                await cursor.execute(
                    """
                    UPDATE drafts
                    SET status = 'sending', send_state = 'sending', updated_at = %s
                    WHERE user_uid = %s AND id = %s
                    """,
                    (now, tenant.user_uid, draft_id),
                )
                await cursor.execute(
                    """
                    UPDATE mail_operations
                    SET status = 'applying', attempt_count = attempt_count + 1,
                        updated_at = %s
                    WHERE user_uid = %s AND id = %s
                    """,
                    (now, tenant.user_uid, operation_id),
                )
            await uow.commit()
            return _DeliveryStart(
                draft=draft,
                operation_id=operation_id,
                attempt_id=attempt_id,
                attempt_number=attempt_number,
            )

    async def _load_and_compose(
        self,
        tenant: TenantContext,
        draft: _DraftScope,
        *,
        now: float,
    ) -> tuple[SendCommand, object]:
        async with self.pool.acquire() as connection:
            row = await fetch_one(
                connection,
                """
                SELECT d.subject, d.body_text_object_sha256,
                       d.body_html_object_sha256, d.reply_to_message_id,
                       d.created_at, i.from_address, i.display_name,
                       i.reply_to, i.is_verified, i.account_id AS identity_account,
                       a.provider_key
                FROM drafts d
                JOIN mail_identities i
                  ON i.id = d.identity_id AND i.user_uid = d.user_uid
                JOIN mail_accounts a
                  ON a.id = d.account_id AND a.user_uid = d.user_uid
                WHERE d.user_uid = %s AND d.id = %s
                """,
                (tenant.user_uid, draft.draft_id),
            )
            recipients = await fetch_all(
                connection,
                """
                SELECT recipient_kind, address, display_name
                FROM draft_recipients
                WHERE user_uid = %s AND draft_id = %s
                ORDER BY FIELD(recipient_kind, 'to', 'cc', 'bcc'), position_index, id
                """,
                (tenant.user_uid, draft.draft_id),
            )
            attachments = await fetch_all(
                connection,
                """
                SELECT content_sha256, filename, content_type, size_bytes
                FROM draft_attachments
                WHERE user_uid = %s AND draft_id = %s
                ORDER BY position_index, id
                """,
                (tenant.user_uid, draft.draft_id),
            )
            reply = None
            if row is not None and row["reply_to_message_id"]:
                reply = await fetch_one(
                    connection,
                    """
                    SELECT m.message_id_header, h.in_reply_to, h.references_json
                    FROM messages m
                    LEFT JOIN message_headers h
                      ON h.message_id = m.id AND h.user_uid = m.user_uid
                    WHERE m.user_uid = %s AND m.id = %s
                    """,
                    (tenant.user_uid, str(row["reply_to_message_id"])),
                )
        if row is None:
            raise NotFoundError("draft composition data was not found")
        if not bool(row["is_verified"]) or str(row["identity_account"]) != draft.account_id:
            raise ConflictError("sender identity is not available")
        text_body = ""
        html_body = ""
        if row["body_text_object_sha256"]:
            text_body = (await self._read_object(tenant, str(row["body_text_object_sha256"]))).decode(
                "utf-8",
                errors="replace",
            )
        if row["body_html_object_sha256"]:
            html_body = (await self._read_object(tenant, str(row["body_html_object_sha256"]))).decode(
                "utf-8",
                errors="replace",
            )
        composed_attachments = []
        for attachment in attachments:
            content = await self._read_object(tenant, str(attachment["content_sha256"]))
            if len(content) != int(attachment["size_bytes"] or 0):
                raise PermanentError("draft attachment size does not match object")
            composed_attachments.append(
                ComposedAttachment(
                    filename=str(attachment["filename"]),
                    content_type=str(attachment["content_type"]),
                    content=content,
                )
            )
        references: list[str] = []
        in_reply_to = ""
        if reply is not None:
            raw_references = reply["references_json"]
            if isinstance(raw_references, str):
                decoded = json.loads(raw_references or "[]")
            else:
                decoded = raw_references or []
            if isinstance(decoded, list):
                references.extend(str(value) for value in decoded if str(value).strip())
            parent_message_id = str(reply["message_id_header"] or "").strip()
            if parent_message_id:
                in_reply_to = parent_message_id
                if parent_message_id not in references:
                    references.append(parent_message_id)
        command = SendCommand(
            draft_id=draft.draft_id,
            message_id_header=draft.message_id_header,
            created_at=float(row["created_at"] or draft.created_at or now),
            from_address=str(row["from_address"]),
            from_display_name=str(row["display_name"] or ""),
            reply_to=str(row["reply_to"] or ""),
            recipients=tuple(
                SendRecipient(
                    str(item["recipient_kind"]),
                    str(item["address"]),
                    str(item["display_name"] or ""),
                )
                for item in recipients
            ),
            subject=str(row["subject"] or ""),
            text_body=text_body,
            html_body=html_body,
            attachments=tuple(composed_attachments),
            in_reply_to=in_reply_to,
            references=tuple(references),
        )
        composed = MimeComposer.compose(command)
        if draft.composed_object_sha256:
            source = await self._read_object(tenant, draft.composed_object_sha256)
            composed = type(composed)(
                message_id_header=composed.message_id_header,
                envelope_from=composed.envelope_from,
                envelope_recipients=composed.envelope_recipients,
                source=source,
                requires_smtp_utf8=composed.requires_smtp_utf8,
            )
            return command, composed
        stored = await self.store.put_stream(
            ObjectKind.RAW_EML,
            _single_chunk(composed.source),
            expected_size=len(composed.source),
        )
        await self._attach_composed_source(tenant, draft.draft_id, stored, now=now)
        return command, composed

    async def _attach_composed_source(
        self,
        tenant: TenantContext,
        draft_id: str,
        stored: StoredObject,
        *,
        now: float,
    ) -> None:
        async with SqlUnitOfWork(self.pool) as uow:
            connection = self._connection(uow)
            repository = ObjectRepository(connection)
            async with repository.lock_object(stored.content_sha256):
                await repository.attach_reference(
                    stored,
                    user_uid=tenant.user_uid,
                    reference_kind="raw_eml",
                    reference_id=draft_id,
                    pinned=True,
                    last_accessed_at=now,
                )
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        UPDATE drafts
                        SET composed_object_sha256 = %s, updated_at = %s
                        WHERE user_uid = %s AND id = %s
                          AND composed_object_sha256 IS NULL
                        """,
                        (stored.content_sha256, now, tenant.user_uid, draft_id),
                    )
                    if cursor.rowcount != 1:
                        current = await fetch_one(
                            connection,
                            "SELECT composed_object_sha256 FROM drafts WHERE user_uid=%s AND id=%s",
                            (tenant.user_uid, draft_id),
                        )
                        if current is None or str(current["composed_object_sha256"]) != stored.content_sha256:
                            raise ConflictError("draft source changed concurrently")
                await uow.commit()

    async def _mark_verification_required(
        self,
        tenant: TenantContext,
        start: _DeliveryStart,
        *,
        now: float,
    ) -> None:
        async with SqlUnitOfWork(self.pool) as uow:
            connection = self._connection(uow)
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    UPDATE send_attempts
                    SET status = 'verification_required',
                        safe_response = 'SMTP acceptance could not be determined',
                        finished_at = %s
                    WHERE user_uid = %s AND id = %s AND status = 'sending'
                    """,
                    (now, tenant.user_uid, start.attempt_id),
                )
                await cursor.execute(
                    """
                    UPDATE drafts
                    SET status = 'review_required',
                        send_state = 'verification_required', updated_at = %s
                    WHERE user_uid = %s AND id = %s
                    """,
                    (now, tenant.user_uid, start.draft.draft_id),
                )
                await cursor.execute(
                    """
                    UPDATE mail_operations
                    SET status = 'review_required',
                        last_error_class = 'SmtpResultUncertain',
                        last_error_message = 'SMTP acceptance requires verification',
                        updated_at = %s
                    WHERE user_uid = %s AND id = %s
                      AND operation_type = 'send'
                    """,
                    (now, tenant.user_uid, start.operation_id),
                )
                if cursor.rowcount != 1:
                    raise NotFoundError("send operation was not found")
            operation_id = start.operation_id
            await JobRepository(connection).enqueue(
                JobSpec(
                    queue_name="send",
                    job_kind="send.verify",
                    payload={
                        "draft_id": start.draft.draft_id,
                        "operation_id": operation_id,
                    },
                    user_uid=tenant.user_uid,
                    account_id=start.draft.account_id,
                    provider_key=start.draft.provider_key,
                    priority=5,
                    available_at=now,
                    max_attempts=3,
                    dedupe_key=f"send-verify:{operation_id}:{start.attempt_number}",
                ),
                now=now,
            )
            await OutboxRepository(connection, tenant).append(
                "mail.send.verification_required",
                operation_id,
                {
                    "operation_id": operation_id,
                    "draft_id": start.draft.draft_id,
                },
                aggregate_type="mail_operation",
                now=now,
            )
            await uow.commit()

    async def _mark_sent(
        self,
        tenant: TenantContext,
        start: _DeliveryStart,
        *,
        response_code: int | None,
        safe_response: str,
        enqueue_append: bool,
        now: float,
    ) -> None:
        async with SqlUnitOfWork(self.pool) as uow:
            connection = self._connection(uow)
            operation = await fetch_one(
                connection,
                """
                SELECT id FROM mail_operations
                WHERE user_uid = %s AND id = %s
                  AND target_id = %s AND operation_type = 'send'
                FOR UPDATE
                """,
                (
                    tenant.user_uid,
                    start.operation_id,
                    start.draft.draft_id,
                ),
            )
            if operation is None:
                raise NotFoundError("send operation was not found")
            operation_id = start.operation_id
            async with connection.cursor() as cursor:
                if start.attempt_id:
                    await cursor.execute(
                        """
                        UPDATE send_attempts
                        SET status = 'sent', smtp_response_code = %s,
                            safe_response = %s, finished_at = %s
                        WHERE user_uid = %s AND id = %s
                        """,
                        (
                            response_code,
                            str(safe_response or "")[:512],
                            now,
                            tenant.user_uid,
                            start.attempt_id,
                        ),
                    )
                else:
                    await cursor.execute(
                        """
                        UPDATE send_attempts
                        SET status = 'sent', safe_response = %s,
                            finished_at = %s
                        WHERE user_uid = %s AND operation_id = %s
                          AND status = 'verification_required'
                        """,
                        (
                            str(safe_response or "")[:512],
                            now,
                            tenant.user_uid,
                            operation_id,
                        ),
                    )
                await cursor.execute(
                    """
                    UPDATE drafts
                    SET status = 'sent', send_state = 'sent',
                        sent_at = COALESCE(sent_at, %s), updated_at = %s
                    WHERE user_uid = %s AND id = %s
                    """,
                    (now, now, tenant.user_uid, start.draft.draft_id),
                )
                await cursor.execute(
                    """
                    UPDATE mail_operations
                    SET status = 'synced', last_error_class = '',
                        last_error_message = '', completed_at = %s, updated_at = %s
                    WHERE user_uid = %s AND id = %s
                    """,
                    (now, now, tenant.user_uid, operation_id),
                )
            plugin = self.registry.get(start.draft.provider_key)
            if enqueue_append and plugin.sent_copy_strategy() is SentCopyStrategy.IMAP_APPEND:
                await JobRepository(connection).enqueue(
                    JobSpec(
                        queue_name="send",
                        job_kind="send.append_sent_copy",
                        payload={
                            "draft_id": start.draft.draft_id,
                            "operation_id": operation_id,
                        },
                        user_uid=tenant.user_uid,
                        account_id=start.draft.account_id,
                        provider_key=start.draft.provider_key,
                        priority=20,
                        available_at=now,
                        max_attempts=5,
                        dedupe_key=f"send-append:{operation_id}",
                    ),
                    now=now,
                )
            await OutboxRepository(connection, tenant).append(
                "mail.send.sent",
                operation_id,
                {
                    "operation_id": operation_id,
                    "draft_id": start.draft.draft_id,
                },
                aggregate_type="mail_operation",
                now=now,
            )
            await uow.commit()

    async def _mark_delivery_failure(
        self,
        tenant: TenantContext,
        start: _DeliveryStart,
        *,
        permanent: bool,
        now: float,
    ) -> None:
        operation_status = "failed" if permanent else "retry_wait"
        async with SqlUnitOfWork(self.pool) as uow:
            connection = self._connection(uow)
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    UPDATE send_attempts
                    SET status = 'failed',
                        safe_response = 'SMTP delivery failed', finished_at = %s
                    WHERE user_uid = %s AND id = %s
                    """,
                    (now, tenant.user_uid, start.attempt_id),
                )
                await cursor.execute(
                    """
                    UPDATE drafts
                    SET status = 'failed', send_state = 'failed', updated_at = %s
                    WHERE user_uid = %s AND id = %s
                    """,
                    (now, tenant.user_uid, start.draft.draft_id),
                )
                await cursor.execute(
                    """
                    UPDATE mail_operations
                    SET status = %s, last_error_class = %s,
                        last_error_message = 'SMTP delivery failed', updated_at = %s,
                        completed_at = CASE WHEN %s = 'failed' THEN %s ELSE NULL END
                    WHERE user_uid = %s AND id = %s
                      AND operation_type = 'send'
                    """,
                    (
                        operation_status,
                        "PermanentSmtpFailure" if permanent else "RetryableSmtpFailure",
                        now,
                        operation_status,
                        now,
                        tenant.user_uid,
                        start.operation_id,
                    ),
                )
            await uow.commit()

    async def _handle_verification_miss(
        self,
        tenant: TenantContext,
        draft_id: str,
        operation_id: str,
        context: JobContext,
        *,
        now: float,
    ) -> None:
        async with SqlUnitOfWork(self.pool) as uow:
            connection = self._connection(uow)
            row = await fetch_one(
                connection,
                """
                SELECT d.verification_attempts, d.account_id, a.provider_key
                FROM drafts d
                JOIN mail_accounts a
                  ON a.id = d.account_id AND a.user_uid = d.user_uid
                WHERE d.user_uid = %s AND d.id = %s
                FOR UPDATE
                """,
                (tenant.user_uid, draft_id),
            )
            if row is None:
                raise NotFoundError("draft was not found")
            count = int(row["verification_attempts"] or 0) + 1
            retry = count <= self.verification_retry_limit
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    UPDATE send_attempts
                    SET status = 'failed',
                        safe_response = 'message not found in Sent mailbox',
                        finished_at = %s
                    WHERE user_uid = %s AND operation_id = %s
                      AND status = 'verification_required'
                    """,
                    (now, tenant.user_uid, operation_id),
                )
                await cursor.execute(
                    """
                    UPDATE drafts
                    SET status = %s, send_state = %s,
                        verification_attempts = %s, updated_at = %s
                    WHERE user_uid = %s AND id = %s
                    """,
                    (
                        "queued" if retry else "review_required",
                        "queued" if retry else "review_required",
                        count,
                        now,
                        tenant.user_uid,
                        draft_id,
                    ),
                )
                await cursor.execute(
                    """
                    UPDATE mail_operations
                    SET status = %s, last_error_class = %s,
                        last_error_message = %s, updated_at = %s
                    WHERE user_uid = %s AND id = %s
                    """,
                    (
                        "pending" if retry else "review_required",
                        "SentVerificationMiss" if retry else "SentVerificationExhausted",
                        "controlled SMTP retry queued" if retry else "manual review required",
                        now,
                        tenant.user_uid,
                        operation_id,
                    ),
                )
            if retry:
                await JobRepository(connection).enqueue(
                    JobSpec(
                        queue_name="send",
                        job_kind="send.deliver",
                        payload={"draft_id": draft_id, "operation_id": operation_id},
                        user_uid=tenant.user_uid,
                        account_id=str(row["account_id"]),
                        provider_key=str(row["provider_key"]),
                        priority=10,
                        available_at=now,
                        max_attempts=3,
                        dedupe_key=f"send-deliver:{operation_id}:{count}",
                    ),
                    now=now,
                )
            await OutboxRepository(connection, tenant).append(
                "mail.send.verification_miss",
                operation_id,
                {
                    "operation_id": operation_id,
                    "draft_id": draft_id,
                    "verification_attempt": count,
                    "retry_queued": retry,
                },
                aggregate_type="mail_operation",
                now=now,
            )
            await uow.commit()

    async def _read_object(self, tenant: TenantContext, digest: str) -> bytes:
        async with self.pool.acquire() as connection:
            row = await fetch_one(
                connection,
                """
                SELECT o.compression
                FROM content_objects o
                WHERE o.content_sha256 = %s
                  AND EXISTS (
                      SELECT 1 FROM content_references r
                      WHERE r.user_uid = %s
                        AND r.content_sha256 = o.content_sha256
                  )
                """,
                (digest, tenant.user_uid),
            )
        if row is None:
            raise NotFoundError("content object was not found")
        async with self.store.open(digest) as handle:
            data = handle.read()
        compression = str(row["compression"] or "none")
        if compression == "gzip":
            return gzip.decompress(data)
        if compression != "none":
            raise PermanentError("unsupported content compression")
        return data

    @staticmethod
    def _job_scope(
        context: JobContext,
        payload: Mapping[str, object],
    ) -> tuple[TenantContext, str, str]:
        if not isinstance(context, JobContext):
            raise TypeError("context must be JobContext")
        user_uid = _required_text(context.user_uid, "user_uid")
        draft_id = _required_text(payload.get("draft_id"), "draft_id")
        operation_id = _required_text(payload.get("operation_id"), "operation_id")
        return TenantContext(user_uid), draft_id, operation_id

    @staticmethod
    def _connection(uow: SqlUnitOfWork) -> aiomysql.Connection:
        if uow.connection is None:
            raise RuntimeError("unit of work connection is unavailable")
        return uow.connection
