"""Tenant-scoped thread projections, detail structure, and local body access."""

from __future__ import annotations

import base64
import gzip
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import AsyncIterator

import aiomysql

from flymail.api.schemas.threads import (
    ThreadAttachment,
    ThreadDetailResponse,
    ThreadListItem,
    ThreadListResponse,
    ThreadMembership,
    ThreadMessage,
    ThreadOperation,
)
from flymail.application.auth import AuthenticatedSession
from flymail.domain.errors import ApiContractError, ConflictError, NotFoundError
from flymail.infrastructure.db.pool import DatabasePool
from flymail.infrastructure.object_store.models import ObjectVerificationStatus
from flymail.infrastructure.object_store.store import ObjectStore
from flymail.repositories.base import TenantContext
from flymail.repositories.jobs import JobRepository, JobSpec


@dataclass(frozen=True, slots=True)
class BodyContent:
    message_id: str
    content_sha256: str
    content_type: str
    compression: str


@dataclass(frozen=True, slots=True)
class BodyQueue:
    message_id: str
    state: str
    job_id: str


def _decode_array(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return ()
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value if str(item or "").strip())


def _split_ids(value: object) -> tuple[str, ...]:
    return tuple(item for item in str(value or "").split(",") if item)


class ThreadCursorCodec:
    """Authenticate cursor positions so clients cannot forge SQL seek values."""

    def __init__(self, secret: str) -> None:
        normalized = str(secret or "")
        if len(normalized) < 16:
            raise ValueError("cursor secret must be at least 16 characters")
        self.key = hmac.new(
            normalized.encode("utf-8"),
            b"flymail-v2/thread-cursor/v1",
            hashlib.sha256,
        ).digest()

    @staticmethod
    def _b64encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")

    @staticmethod
    def _b64decode(value: str) -> bytes:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

    def encode(self, latest_message_at: float, thread_id: str) -> str:
        payload = json.dumps(
            {"v": 1, "latest_message_at": float(latest_message_at), "thread_id": str(thread_id)},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        signature = hmac.new(self.key, payload, hashlib.sha256).digest()
        return f"{self._b64encode(payload)}.{self._b64encode(signature)}"

    def decode(self, value: str | None) -> tuple[float, str] | None:
        if value in (None, ""):
            return None
        try:
            encoded, encoded_signature = str(value).split(".", 1)
            payload = self._b64decode(encoded)
            signature = self._b64decode(encoded_signature)
            expected = hmac.new(self.key, payload, hashlib.sha256).digest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError
            decoded = json.loads(payload.decode("utf-8"))
            if not isinstance(decoded, dict) or decoded.get("v") != 1:
                raise ValueError
            timestamp = float(decoded["latest_message_at"])
            thread_id = str(decoded["thread_id"] or "").strip()
            if timestamp < 0 or not thread_id:
                raise ValueError
            return timestamp, thread_id
        except (ValueError, TypeError, KeyError, json.JSONDecodeError, UnicodeDecodeError):
            raise ApiContractError(
                "invalid_cursor",
                "分页游标无效",
                status_code=400,
            ) from None


class ThreadQueryService:
    def __init__(
        self,
        pool: DatabasePool,
        store: ObjectStore,
        cursor_secret: str,
        *,
        now_fn=time.time,
    ) -> None:
        if not isinstance(pool, DatabasePool):
            raise TypeError("pool must be DatabasePool")
        if not isinstance(store, ObjectStore):
            raise TypeError("store must be ObjectStore")
        self.pool = pool
        self.store = store
        self.cursor_codec = ThreadCursorCodec(cursor_secret)
        self.now_fn = now_fn

    async def list_threads(
        self,
        session: AuthenticatedSession,
        *,
        semantic_mailbox: str,
        limit: int,
        cursor: str | None,
        account_id: str | None,
        native_label: str | None,
        unread: bool | None,
        starred: bool | None,
        has_attachment: bool | None,
    ) -> ThreadListResponse:
        mailbox = str(semantic_mailbox or "inbox").strip().casefold()
        if not mailbox or len(mailbox) > 64:
            raise ApiContractError("validation_error", "邮箱分类无效", status_code=422)
        page_size = min(max(int(limit), 1), 100)
        position = self.cursor_codec.decode(cursor)
        conditions = ["p.user_uid = %s", "p.semantic_mailbox = %s"]
        params: list[object] = [session.user.id, mailbox]
        if position is not None:
            conditions.append(
                "(p.latest_message_at < %s OR (p.latest_message_at = %s AND p.thread_id < %s))"
            )
            params.extend((position[0], position[0], position[1]))
        if unread is True:
            conditions.append("p.unread_count > 0")
        elif unread is False:
            conditions.append("p.unread_count = 0")
        if starred is not None:
            conditions.append("p.is_starred = %s")
            params.append(1 if starred else 0)
        if has_attachment is not None:
            conditions.append("p.has_attachments = %s")
            params.append(1 if has_attachment else 0)
        if account_id:
            conditions.append(
                """
                EXISTS (
                    SELECT 1
                    FROM thread_messages tf
                    JOIN message_remote_instances rf
                      ON rf.message_id = tf.message_id AND rf.user_uid = tf.user_uid
                     AND rf.remote_deleted = 0
                    WHERE tf.user_uid = p.user_uid AND tf.thread_id = p.thread_id
                      AND rf.account_id = %s
                )
                """
            )
            params.append(str(account_id).strip())
        if native_label:
            conditions.append(
                """
                EXISTS (
                    SELECT 1
                    FROM thread_messages tl
                    JOIN message_remote_instances rl
                      ON rl.message_id = tl.message_id AND rl.user_uid = tl.user_uid
                     AND rl.remote_deleted = 0
                    JOIN message_memberships ml
                      ON ml.remote_instance_id = rl.id AND ml.user_uid = rl.user_uid
                    JOIN mailboxes bl
                      ON bl.id = ml.mailbox_id AND bl.user_uid = ml.user_uid
                    WHERE tl.user_uid = p.user_uid AND tl.thread_id = p.thread_id
                      AND bl.id = %s AND bl.mailbox_type = 'label'
                )
                """
            )
            params.append(str(native_label).strip())
        params.append(page_size + 1)
        sql = f"""
            SELECT p.thread_id, p.latest_message_id, p.latest_message_at,
                   p.subject, p.participants_summary, p.latest_snippet,
                   p.message_count, p.unread_count, p.is_starred,
                   p.has_attachments, p.account_count,
                   p.pending_operation_count, p.projection_version
            FROM thread_projections p FORCE INDEX (idx_thread_projection_cursor)
            WHERE {' AND '.join(conditions)}
            ORDER BY p.latest_message_at DESC, p.thread_id DESC
            LIMIT %s
        """
        async with self.pool.acquire() as connection:
            async with connection.cursor(aiomysql.DictCursor) as db_cursor:
                await db_cursor.execute(sql, tuple(params))
                rows = [dict(row) for row in await db_cursor.fetchall()]
                visible_rows = rows[:page_size]
                account_ids_by_thread: dict[str, str] = {}
                if visible_rows:
                    thread_ids = tuple(str(row["thread_id"]) for row in visible_rows)
                    placeholders = ",".join("%s" for _ in thread_ids)
                    await db_cursor.execute(
                        f"""
                        SELECT tm.thread_id,
                               GROUP_CONCAT(
                                   DISTINCT ri.account_id
                                   ORDER BY ri.account_id SEPARATOR ','
                               ) AS account_ids
                        FROM thread_messages tm
                        JOIN message_remote_instances ri
                          FORCE INDEX (idx_remote_instances_message)
                          ON ri.message_id = tm.message_id
                         AND ri.user_uid = tm.user_uid
                         AND ri.remote_deleted = 0
                        WHERE tm.user_uid = %s
                          AND tm.thread_id IN ({placeholders})
                        GROUP BY tm.thread_id
                        """,
                        (session.user.id, *thread_ids),
                    )
                    account_ids_by_thread = {
                        str(row["thread_id"]): str(row["account_ids"] or "")
                        for row in await db_cursor.fetchall()
                    }
        has_more = len(rows) > page_size
        visible = [
            {**row, "account_ids": account_ids_by_thread.get(str(row["thread_id"]), "")}
            for row in rows[:page_size]
        ]
        items = tuple(
            ThreadListItem(
                id=str(row["thread_id"]),
                latest_message_id=str(row["latest_message_id"]),
                latest_message_at=float(row["latest_message_at"] or 0),
                subject=str(row["subject"] or ""),
                participants_summary=str(row["participants_summary"] or ""),
                latest_snippet=str(row["latest_snippet"] or ""),
                message_count=max(int(row["message_count"] or 0), 0),
                unread_count=max(int(row["unread_count"] or 0), 0),
                is_starred=bool(row["is_starred"]),
                has_attachments=bool(row["has_attachments"]),
                account_count=max(int(row["account_count"] or 0), 0),
                account_ids=_split_ids(row["account_ids"]),
                pending_operation_count=max(int(row["pending_operation_count"] or 0), 0),
                projection_version=max(int(row["projection_version"] or 1), 1),
            )
            for row in visible
        )
        next_cursor = None
        if has_more and items:
            last = items[-1]
            next_cursor = self.cursor_codec.encode(last.latest_message_at, last.id)
        return ThreadListResponse(items=items, next_cursor=next_cursor)

    async def get_thread(
        self,
        session: AuthenticatedSession,
        thread_id: str,
    ) -> ThreadDetailResponse:
        tenant = TenantContext(session.user.id)
        normalized_id = str(thread_id or "").strip()
        async with self.pool.acquire() as connection:
            async with connection.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(
                    """
                    SELECT id, normalized_subject, created_at, updated_at
                    FROM threads
                    WHERE id = %s AND user_uid = %s
                    """,
                    (normalized_id, tenant.user_uid),
                )
                thread = await cursor.fetchone()
                if thread is None:
                    raise NotFoundError("thread was not found")
                await cursor.execute(
                    """
                    SELECT m.id, m.subject, m.from_json, m.to_json, m.cc_json,
                           m.reply_to_json, m.sent_at, m.received_at, m.size_bytes,
                           m.has_attachments, m.snippet,
                           COALESCE(b.state, m.body_state) AS body_state,
                           m.search_state
                    FROM thread_messages tm
                    JOIN messages m
                      ON m.id = tm.message_id AND m.user_uid = tm.user_uid
                    LEFT JOIN message_bodies b
                      ON b.message_id = m.id AND b.user_uid = m.user_uid
                    WHERE tm.thread_id = %s AND tm.user_uid = %s
                    ORDER BY m.received_at ASC, m.id ASC
                    """,
                    (normalized_id, tenant.user_uid),
                )
                message_rows = [dict(row) for row in await cursor.fetchall()]
                message_ids = [str(row["id"]) for row in message_rows]
                memberships: dict[str, list[ThreadMembership]] = {value: [] for value in message_ids}
                attachments: dict[str, list[ThreadAttachment]] = {value: [] for value in message_ids}
                operations: dict[str, list[ThreadOperation]] = {value: [] for value in message_ids}
                source_accounts: dict[str, set[str]] = {value: set() for value in message_ids}
                if message_ids:
                    placeholders = ",".join("%s" for _ in message_ids)
                    await cursor.execute(
                        f"""
                        SELECT ri.message_id, ri.account_id, mb.id AS mailbox_id,
                               mb.native_name, mb.semantic_key,
                               mm.membership_kind, mm.provider_label
                        FROM message_remote_instances ri
                        JOIN message_memberships mm
                          ON mm.remote_instance_id = ri.id AND mm.user_uid = ri.user_uid
                        JOIN mailboxes mb
                          ON mb.id = mm.mailbox_id AND mb.user_uid = mm.user_uid
                        WHERE ri.user_uid = %s AND ri.message_id IN ({placeholders})
                          AND ri.remote_deleted = 0
                        ORDER BY ri.message_id, ri.account_id, mb.native_name, mb.id
                        """,
                        (tenant.user_uid, *message_ids),
                    )
                    for row in await cursor.fetchall():
                        message_id = str(row["message_id"])
                        source_accounts[message_id].add(str(row["account_id"]))
                        memberships[message_id].append(
                            ThreadMembership(
                                account_id=str(row["account_id"]),
                                mailbox_id=str(row["mailbox_id"]),
                                native_name=str(row["native_name"] or ""),
                                semantic_key=str(row["semantic_key"] or "custom"),
                                membership_kind=str(row["membership_kind"] or "folder"),
                                provider_label=str(row["provider_label"] or ""),
                            )
                        )
                    await cursor.execute(
                        f"""
                        SELECT id, message_id, filename, content_type,
                               disposition, remote_size_bytes, is_inline, cache_state
                        FROM message_attachments
                        WHERE user_uid = %s AND message_id IN ({placeholders})
                        ORDER BY message_id, id
                        """,
                        (tenant.user_uid, *message_ids),
                    )
                    for row in await cursor.fetchall():
                        attachments[str(row["message_id"])].append(
                            ThreadAttachment(
                                id=str(row["id"]),
                                filename=str(row["filename"] or ""),
                                content_type=str(row["content_type"] or "application/octet-stream"),
                                disposition=str(row["disposition"] or "attachment"),
                                remote_size_bytes=max(int(row["remote_size_bytes"] or 0), 0),
                                is_inline=bool(row["is_inline"]),
                                cache_state=str(row["cache_state"] or "not_requested"),
                            )
                        )
                    await cursor.execute(
                        f"""
                        SELECT id, target_id, operation_type, status,
                               account_id, remote_instance_id, created_at, updated_at
                        FROM mail_operations
                        WHERE user_uid = %s AND target_type = 'message'
                          AND target_id IN ({placeholders})
                        ORDER BY target_id, created_at, id
                        """,
                        (tenant.user_uid, *message_ids),
                    )
                    for row in await cursor.fetchall():
                        operations[str(row["target_id"])].append(
                            ThreadOperation(
                                id=str(row["id"]),
                                operation_type=str(row["operation_type"]),
                                status=str(row["status"]),
                                account_id=str(row["account_id"]) if row["account_id"] else None,
                                remote_instance_id=(
                                    str(row["remote_instance_id"])
                                    if row["remote_instance_id"]
                                    else None
                                ),
                                created_at=float(row["created_at"] or 0),
                                updated_at=float(row["updated_at"] or 0),
                            )
                        )
        return ThreadDetailResponse(
            id=str(thread["id"]),
            normalized_subject=str(thread["normalized_subject"] or ""),
            created_at=float(thread["created_at"] or 0),
            updated_at=float(thread["updated_at"] or 0),
            messages=tuple(
                ThreadMessage(
                    id=str(row["id"]),
                    subject=str(row["subject"] or ""),
                    from_addresses=_decode_array(row["from_json"]),
                    to_addresses=_decode_array(row["to_json"]),
                    cc_addresses=_decode_array(row["cc_json"]),
                    reply_to_addresses=_decode_array(row["reply_to_json"]),
                    sent_at=float(row["sent_at"] or 0),
                    received_at=float(row["received_at"] or 0),
                    size_bytes=max(int(row["size_bytes"] or 0), 0),
                    has_attachments=bool(row["has_attachments"]),
                    snippet=str(row["snippet"] or ""),
                    body_state=str(row["body_state"] or "not_requested"),
                    search_state=str(row["search_state"] or "metadata"),
                    source_account_ids=tuple(sorted(source_accounts[str(row["id"])])),
                    memberships=tuple(memberships[str(row["id"])]),
                    attachments=tuple(attachments[str(row["id"])]),
                    operations=tuple(operations[str(row["id"])]),
                )
                for row in message_rows
            ),
        )

    async def resolve_body(
        self,
        session: AuthenticatedSession,
        message_id: str,
    ) -> BodyContent | BodyQueue:
        tenant = TenantContext(session.user.id)
        normalized_id = str(message_id or "").strip()
        async with self.pool.acquire() as connection:
            async with connection.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(
                    """
                    SELECT m.id, COALESCE(b.state, m.body_state) AS state,
                           b.html_object_sha256, b.text_object_sha256,
                           html.compression AS html_compression,
                           text_obj.compression AS text_compression,
                           ri.id AS remote_instance_id, ri.account_id,
                           a.provider_key
                    FROM messages m
                    LEFT JOIN message_bodies b
                      ON b.message_id = m.id AND b.user_uid = m.user_uid
                    LEFT JOIN content_objects html
                      ON html.content_sha256 = b.html_object_sha256
                    LEFT JOIN content_objects text_obj
                      ON text_obj.content_sha256 = b.text_object_sha256
                    LEFT JOIN message_remote_instances ri
                      ON ri.message_id = m.id AND ri.user_uid = m.user_uid
                     AND ri.remote_deleted = 0
                    LEFT JOIN mail_accounts a
                      ON a.id = ri.account_id AND a.user_uid = ri.user_uid
                     AND a.status = 'active'
                    WHERE m.id = %s AND m.user_uid = %s
                    ORDER BY ri.last_seen_at DESC, ri.id DESC
                    LIMIT 1
                    """,
                    (normalized_id, tenant.user_uid),
                )
                row = await cursor.fetchone()
        if row is None:
            raise NotFoundError("message was not found")
        digest = row["html_object_sha256"] or row["text_object_sha256"]
        if str(row["state"] or "") == "ready" and digest:
            verification = await self.store.verify(str(digest))
            if verification.status == ObjectVerificationStatus.READY:
                is_html = bool(row["html_object_sha256"])
                return BodyContent(
                    message_id=normalized_id,
                    content_sha256=str(digest),
                    content_type="text/html; charset=utf-8" if is_html else "text/plain; charset=utf-8",
                    compression=str(
                        row["html_compression"] if is_html else row["text_compression"]
                    ) or "none",
                )
        if str(row["state"] or "") == "unavailable":
            raise ConflictError("message body is unavailable")
        if not row["remote_instance_id"] or not row["account_id"] or not row["provider_key"]:
            raise ConflictError("message body cannot be fetched without an active account")
        timestamp = float(self.now_fn())
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        INSERT INTO message_bodies (
                            message_id, user_uid, state, body_size_bytes,
                            index_version, parser_version, checked_at,
                            last_accessed_at, updated_at
                        ) VALUES (%s, %s, 'queued', 0, 0, 1, 0, 0, %s)
                        AS incoming
                        ON DUPLICATE KEY UPDATE
                            state = CASE
                                WHEN message_bodies.state IN ('not_requested', 'ready', 'evicted', 'failed')
                                THEN 'queued' ELSE message_bodies.state END,
                            updated_at = incoming.updated_at
                        """,
                        (normalized_id, tenant.user_uid, timestamp),
                    )
                    await cursor.execute(
                        """
                        UPDATE messages
                        SET body_state = CASE
                                WHEN body_state IN ('not_requested', 'ready', 'evicted', 'failed')
                                THEN 'queued' ELSE body_state END,
                            updated_at = %s
                        WHERE id = %s AND user_uid = %s
                        """,
                        (timestamp, normalized_id, tenant.user_uid),
                    )
                job_id = await JobRepository(connection).enqueue(
                    JobSpec(
                        queue_name="interactive",
                        job_kind="content.body",
                        payload={"message_id": normalized_id},
                        user_uid=tenant.user_uid,
                        account_id=str(row["account_id"]),
                        provider_key=str(row["provider_key"]),
                        priority=10,
                        available_at=timestamp,
                        max_attempts=10,
                        dedupe_key=f"content.body:{tenant.user_uid}:{normalized_id}",
                    ),
                    now=timestamp,
                )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
        return BodyQueue(message_id=normalized_id, state="queued", job_id=job_id)

    async def stream_body(self, body: BodyContent) -> AsyncIterator[bytes]:
        async with self.store.open(body.content_sha256) as handle:
            reader = gzip.GzipFile(fileobj=handle, mode="rb") if body.compression == "gzip" else handle
            try:
                while True:
                    chunk = reader.read(64 * 1024)
                    if not chunk:
                        break
                    yield chunk
            finally:
                if reader is not handle:
                    reader.close()
