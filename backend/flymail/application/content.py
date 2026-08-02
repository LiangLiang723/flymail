"""Tenant-scoped local attachment and raw-message content access."""

from __future__ import annotations

import gzip
import time
from dataclasses import dataclass
from typing import AsyncIterator

import aiomysql

from flymail.api.schemas.content import AttachmentMetadataResponse, RawMessageStatusResponse
from flymail.application.auth import AuthenticatedSession
from flymail.domain.errors import ApiContractError, ConflictError, NotFoundError
from flymail.infrastructure.db.pool import DatabasePool
from flymail.infrastructure.object_store.models import ObjectVerificationStatus
from flymail.infrastructure.object_store.store import ObjectStore
from flymail.repositories.base import TenantContext
from flymail.repositories.jobs import JobRepository, JobSpec


@dataclass(frozen=True, slots=True)
class StoredContent:
    resource_id: str
    content_sha256: str
    content_type: str
    filename: str
    original_size_bytes: int
    compression: str


@dataclass(frozen=True, slots=True)
class QueuedContent:
    resource_id: str
    state: str
    job_id: str


class ContentApiService:
    """Serve verified local objects or enqueue existing content Worker jobs."""

    def __init__(
        self,
        pool: DatabasePool,
        store: ObjectStore,
        *,
        now_fn=time.time,
    ) -> None:
        if not isinstance(pool, DatabasePool):
            raise TypeError("pool must be DatabasePool")
        if not isinstance(store, ObjectStore):
            raise TypeError("store must be ObjectStore")
        self.pool = pool
        self.store = store
        self.now_fn = now_fn

    async def attachment_metadata(
        self,
        session: AuthenticatedSession,
        attachment_id: str,
    ) -> AttachmentMetadataResponse:
        row = await self._attachment_row(session.user.id, attachment_id)
        return AttachmentMetadataResponse(
            id=str(row["id"]),
            message_id=str(row["message_id"]),
            filename=str(row["filename"] or ""),
            content_type=str(row["content_type"] or "application/octet-stream"),
            disposition=str(row["disposition"] or "attachment"),
            remote_size_bytes=max(int(row["remote_size_bytes"] or 0), 0),
            is_inline=bool(row["is_inline"]),
            cache_state=str(row["cache_state"] or "not_requested"),
        )

    async def resolve_attachment(
        self,
        session: AuthenticatedSession,
        attachment_id: str,
    ) -> StoredContent | QueuedContent:
        row = await self._attachment_row(session.user.id, attachment_id)
        digest = str(row["content_sha256"] or "")
        if digest and str(row["cache_state"] or "") == "ready":
            verification = await self.store.verify(
                digest,
                expected_size=int(row["stored_size_bytes"] or 0),
            )
            if verification.status == ObjectVerificationStatus.READY:
                return StoredContent(
                    resource_id=str(row["id"]),
                    content_sha256=digest,
                    content_type=str(row["content_type"] or "application/octet-stream"),
                    filename=str(row["filename"] or "attachment"),
                    original_size_bytes=max(int(row["original_size_bytes"] or 0), 0),
                    compression=str(row["compression"] or "none"),
                )
        return await self.request_attachment(session, attachment_id)

    async def request_attachment(
        self,
        session: AuthenticatedSession,
        attachment_id: str,
    ) -> QueuedContent:
        tenant = TenantContext(session.user.id)
        normalized_id = str(attachment_id or "").strip()
        timestamp = float(self.now_fn())
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                async with connection.cursor(aiomysql.DictCursor) as cursor:
                    await cursor.execute(
                        """
                        SELECT att.id, att.cache_state, att.remote_instance_id,
                               ri.account_id, a.provider_key
                        FROM message_attachments att
                        JOIN message_remote_instances ri
                          ON ri.id = att.remote_instance_id
                         AND ri.user_uid = att.user_uid
                         AND ri.remote_deleted = 0
                        JOIN mail_accounts a
                          ON a.id = ri.account_id AND a.user_uid = ri.user_uid
                         AND a.status = 'active'
                        WHERE att.id = %s AND att.user_uid = %s
                        FOR UPDATE
                        """,
                        (normalized_id, tenant.user_uid),
                    )
                    row = await cursor.fetchone()
                    if row is None:
                        raise NotFoundError("attachment was not found")
                    await cursor.execute(
                        """
                        UPDATE message_attachments
                        SET cache_state = CASE
                                WHEN cache_state IN ('not_requested', 'ready', 'evicted', 'failed')
                                THEN 'queued' ELSE cache_state END,
                            updated_at = %s
                        WHERE id = %s AND user_uid = %s
                        """,
                        (timestamp, normalized_id, tenant.user_uid),
                    )
                job_id = await JobRepository(connection).enqueue(
                    JobSpec(
                        queue_name="interactive",
                        job_kind="content.attachment",
                        payload={
                            "attachment_id": normalized_id,
                            "quota_class": "attachment_cache",
                        },
                        user_uid=tenant.user_uid,
                        account_id=str(row["account_id"]),
                        provider_key=str(row["provider_key"]),
                        priority=10,
                        available_at=timestamp,
                        max_attempts=10,
                        dedupe_key=f"content.attachment:{tenant.user_uid}:{normalized_id}",
                    ),
                    now=timestamp,
                )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
        return QueuedContent(normalized_id, "queued", job_id)

    async def raw_status(
        self,
        session: AuthenticatedSession,
        message_id: str,
    ) -> RawMessageStatusResponse:
        row = await self._raw_row(session.user.id, message_id)
        state = "ready" if row["raw_eml_object_sha256"] else "not_requested"
        return RawMessageStatusResponse(
            message_id=str(row["id"]),
            state=state,
            size_bytes=max(int(row["original_size_bytes"] or 0), 0),
        )

    async def resolve_raw(
        self,
        session: AuthenticatedSession,
        message_id: str,
    ) -> StoredContent | QueuedContent:
        row = await self._raw_row(session.user.id, message_id)
        digest = str(row["raw_eml_object_sha256"] or "")
        if digest:
            verification = await self.store.verify(
                digest,
                expected_size=int(row["stored_size_bytes"] or 0),
            )
            if verification.status == ObjectVerificationStatus.READY:
                return StoredContent(
                    resource_id=str(row["id"]),
                    content_sha256=digest,
                    content_type="message/rfc822",
                    filename=f"{row['id']}.eml",
                    original_size_bytes=max(int(row["original_size_bytes"] or 0), 0),
                    compression=str(row["compression"] or "none"),
                )
        return await self.request_raw(session, message_id)

    async def request_raw(
        self,
        session: AuthenticatedSession,
        message_id: str,
    ) -> QueuedContent:
        tenant = TenantContext(session.user.id)
        normalized_id = str(message_id or "").strip()
        timestamp = float(self.now_fn())
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                async with connection.cursor(aiomysql.DictCursor) as cursor:
                    await cursor.execute(
                        """
                        SELECT m.id, ri.account_id, a.provider_key
                        FROM messages m
                        JOIN message_remote_instances ri
                          ON ri.message_id = m.id AND ri.user_uid = m.user_uid
                         AND ri.remote_deleted = 0
                        JOIN mail_accounts a
                          ON a.id = ri.account_id AND a.user_uid = ri.user_uid
                         AND a.status = 'active'
                        WHERE m.id = %s AND m.user_uid = %s
                        ORDER BY ri.last_seen_at DESC, ri.id DESC
                        LIMIT 1
                        FOR UPDATE
                        """,
                        (normalized_id, tenant.user_uid),
                    )
                    row = await cursor.fetchone()
                    if row is None:
                        raise NotFoundError("message was not found")
                    await cursor.execute(
                        """
                        INSERT INTO message_bodies (
                            message_id, user_uid, state, body_size_bytes,
                            index_version, parser_version, checked_at,
                            last_accessed_at, updated_at
                        ) VALUES (%s, %s, 'not_requested', 0, 0, 1, 0, 0, %s)
                        AS incoming
                        ON DUPLICATE KEY UPDATE updated_at = incoming.updated_at
                        """,
                        (normalized_id, tenant.user_uid, timestamp),
                    )
                job_id = await JobRepository(connection).enqueue(
                    JobSpec(
                        queue_name="interactive",
                        job_kind="content.raw_eml",
                        payload={
                            "message_id": normalized_id,
                            "quota_class": "body_cache",
                        },
                        user_uid=tenant.user_uid,
                        account_id=str(row["account_id"]),
                        provider_key=str(row["provider_key"]),
                        priority=10,
                        available_at=timestamp,
                        max_attempts=10,
                        dedupe_key=f"content.raw_eml:{tenant.user_uid}:{normalized_id}",
                    ),
                    now=timestamp,
                )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
        return QueuedContent(normalized_id, "queued", job_id)

    async def stream(
        self,
        content: StoredContent,
        *,
        start: int = 0,
        end: int | None = None,
    ) -> AsyncIterator[bytes]:
        if content.compression != "none" and (start != 0 or end is not None):
            raise ApiContractError(
                "range_not_supported",
                "压缩缓存对象不支持范围下载",
                status_code=416,
            )
        async with self.store.open(content.content_sha256) as handle:
            source = gzip.GzipFile(fileobj=handle, mode="rb") if content.compression == "gzip" else handle
            try:
                if start:
                    source.seek(start)
                remaining = None if end is None else end - start + 1
                while remaining is None or remaining > 0:
                    chunk_size = 64 * 1024 if remaining is None else min(64 * 1024, remaining)
                    chunk = source.read(chunk_size)
                    if not chunk:
                        break
                    if remaining is not None:
                        remaining -= len(chunk)
                    yield chunk
            finally:
                if source is not handle:
                    source.close()

    async def _attachment_row(self, user_uid: str, attachment_id: str) -> dict:
        async with self.pool.acquire() as connection:
            async with connection.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(
                    """
                    SELECT att.id, att.message_id, att.filename, att.content_type,
                           att.disposition, att.remote_size_bytes, att.is_inline,
                           att.cache_state, att.content_sha256,
                           obj.original_size_bytes, obj.stored_size_bytes,
                           obj.compression
                    FROM message_attachments att
                    LEFT JOIN content_objects obj
                      ON obj.content_sha256 = att.content_sha256
                    WHERE att.id = %s AND att.user_uid = %s
                    """,
                    (str(attachment_id or "").strip(), user_uid),
                )
                row = await cursor.fetchone()
        if row is None:
            raise NotFoundError("attachment was not found")
        return dict(row)

    async def _raw_row(self, user_uid: str, message_id: str) -> dict:
        async with self.pool.acquire() as connection:
            async with connection.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(
                    """
                    SELECT m.id, body.raw_eml_object_sha256,
                           obj.original_size_bytes, obj.stored_size_bytes,
                           obj.compression
                    FROM messages m
                    LEFT JOIN message_bodies body
                      ON body.message_id = m.id AND body.user_uid = m.user_uid
                    LEFT JOIN content_objects obj
                      ON obj.content_sha256 = body.raw_eml_object_sha256
                    WHERE m.id = %s AND m.user_uid = %s
                    """,
                    (str(message_id or "").strip(), user_uid),
                )
                row = await cursor.fetchone()
        if row is None:
            raise NotFoundError("message was not found")
        return dict(row)
