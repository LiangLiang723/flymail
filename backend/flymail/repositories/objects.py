"""SQL-only repository for object metadata and business references.

Transaction ownership remains with application and infrastructure services.
Repository methods never begin, commit, or roll back a transaction.
"""

from __future__ import annotations

import re
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

import aiomysql

from flymail.domain.enums import ObjectKind
from flymail.domain.ids import new_id
from flymail.infrastructure.object_store.models import (
    AttachmentEvictionCandidate,
    BodyEvictionCandidate,
    ContentObjectRecord,
    DetachedAttachmentObject,
    DetachedBodyObject,
    StoredObject,
)


_DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}")

REFERENCE_KIND_TO_OBJECT_KIND: dict[str, ObjectKind] = {
    "message_body_html": ObjectKind.BODY_HTML,
    "message_body_text": ObjectKind.BODY_TEXT,
    "message_inline_image": ObjectKind.INLINE_IMAGE,
    "message_attachment": ObjectKind.ATTACHMENT,
    "raw_eml": ObjectKind.RAW_EML,
    "draft_body_html": ObjectKind.BODY_HTML,
    "draft_body_text": ObjectKind.BODY_TEXT,
    "draft_attachment": ObjectKind.DRAFT_ATTACHMENT,
    "user_avatar": ObjectKind.USER_AVATAR,
    "account_icon": ObjectKind.ACCOUNT_ICON,
    "contact_avatar": ObjectKind.CONTACT_AVATAR,
    "notification_asset": ObjectKind.NOTIFICATION_ASSET,
}

BODY_CACHE_REFERENCE_KINDS = frozenset(
    {"message_body_html", "message_body_text", "message_inline_image", "raw_eml"}
)


class ObjectLockUnavailable(RuntimeError):
    """Raised when a cross-process object lease cannot be acquired in time."""


def reference_kinds_for_object_kinds(kinds: set[ObjectKind]) -> tuple[str, ...]:
    return tuple(
        sorted(
            reference_kind
            for reference_kind, object_kind in REFERENCE_KIND_TO_OBJECT_KIND.items()
            if object_kind in kinds
        )
    )


def _normalize_digest(value: str) -> str:
    digest = str(value or "").strip().lower()
    if not _DIGEST_PATTERN.fullmatch(digest):
        raise ValueError("invalid SHA-256 digest")
    return digest


def _required_text(value: str, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{label} is required")
    return normalized


def _object_record(row) -> ContentObjectRecord:
    return ContentObjectRecord(
        content_sha256=str(row[0]),
        object_kind=str(row[1]),
        compression=str(row[2]),
        original_size_bytes=int(row[3] or 0),
        stored_size_bytes=int(row[4] or 0),
        relative_path=str(row[5]),
        verified_at=float(row[6]) if row[6] is not None else None,
        created_at=float(row[7] or 0),
    )


class ObjectRepository:
    def __init__(self, connection: aiomysql.Connection) -> None:
        self.connection = connection

    @asynccontextmanager
    async def lock_object(
        self,
        content_sha256: str,
        *,
        timeout_seconds: int = 30,
    ) -> AsyncIterator[None]:
        digest = _normalize_digest(content_sha256)
        timeout = int(timeout_seconds)
        if timeout < 0:
            raise ValueError("object lock timeout must be non-negative")
        lock_name = f"flymail_v2_obj_{digest[:48]}"
        async with self.connection.cursor() as cursor:
            await cursor.execute("SELECT GET_LOCK(%s, %s)", (lock_name, timeout))
            row = await cursor.fetchone()
        if not row or int(row[0] or 0) != 1:
            raise ObjectLockUnavailable("could not acquire object mutation lock")
        try:
            yield
        finally:
            async with self.connection.cursor() as cursor:
                await cursor.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
                await cursor.fetchone()

    async def attach_reference(
        self,
        stored: StoredObject,
        *,
        user_uid: str,
        reference_kind: str,
        reference_id: str,
        pinned: bool = False,
        last_accessed_at: float = 0,
    ) -> str:
        digest = _normalize_digest(stored.content_sha256)
        user_uid = _required_text(user_uid, "user_uid")
        reference_id = _required_text(reference_id, "reference_id")
        if reference_kind not in REFERENCE_KIND_TO_OBJECT_KIND:
            raise ValueError("unsupported content reference kind")
        if REFERENCE_KIND_TO_OBJECT_KIND[reference_kind] is not stored.kind:
            raise ValueError("stored object kind does not match reference kind")
        if stored.path.is_symlink() or not stored.path.is_file():
            raise FileNotFoundError(str(stored.path))
        if stored.path.stat().st_size != stored.stored_size_bytes:
            raise ValueError("stored object size does not match physical file")

        existing = await self.get_object(digest, for_update=True)
        if existing is None:
            async with self.connection.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO content_objects (
                        content_sha256, object_kind, compression, original_size_bytes,
                        stored_size_bytes, relative_path, verified_at, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, NULL, %s)
                    """,
                    (
                        digest,
                        stored.kind.value,
                        stored.compression,
                        stored.original_size_bytes,
                        stored.stored_size_bytes,
                        stored.relative_path,
                        time.time(),
                    ),
                )
        elif (
            existing.original_size_bytes != stored.original_size_bytes
            or existing.stored_size_bytes != stored.stored_size_bytes
            or existing.relative_path != stored.relative_path
            or existing.compression != stored.compression
        ):
            raise ValueError("content object metadata conflicts with existing digest")

        async with self.connection.cursor() as cursor:
            await cursor.execute(
                """
                SELECT id
                FROM content_references
                WHERE user_uid = %s AND content_sha256 = %s
                  AND reference_kind = %s AND reference_id = %s
                FOR UPDATE
                """,
                (user_uid, digest, reference_kind, reference_id),
            )
            row = await cursor.fetchone()
            accessed_at = float(last_accessed_at or time.time())
            if row:
                reference_uid = str(row[0])
                await cursor.execute(
                    """
                    UPDATE content_references
                    SET pinned = GREATEST(pinned, %s),
                        last_accessed_at = GREATEST(last_accessed_at, %s)
                    WHERE id = %s
                    """,
                    (1 if pinned else 0, accessed_at, reference_uid),
                )
            else:
                reference_uid = new_id("objref")
                await cursor.execute(
                    """
                    INSERT INTO content_references (
                        id, user_uid, content_sha256, reference_kind, reference_id,
                        pinned, created_at, last_accessed_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        reference_uid,
                        user_uid,
                        digest,
                        reference_kind,
                        reference_id,
                        1 if pinned else 0,
                        time.time(),
                        accessed_at,
                    ),
                )
        return reference_uid

    async def detach_reference(
        self,
        *,
        user_uid: str,
        reference_kind: str,
        reference_id: str,
    ) -> str | None:
        user_uid = _required_text(user_uid, "user_uid")
        reference_id = _required_text(reference_id, "reference_id")
        if reference_kind not in REFERENCE_KIND_TO_OBJECT_KIND:
            raise ValueError("unsupported content reference kind")
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                """
                SELECT id, content_sha256
                FROM content_references
                WHERE user_uid = %s AND reference_kind = %s AND reference_id = %s
                LIMIT 1 FOR UPDATE
                """,
                (user_uid, reference_kind, reference_id),
            )
            row = await cursor.fetchone()
            if not row:
                return None
            await cursor.execute("DELETE FROM content_references WHERE id = %s", (row[0],))
            return _normalize_digest(str(row[1]))

    async def count_references(self, content_sha256: str, *, for_update: bool = False) -> int:
        digest = _normalize_digest(content_sha256)
        if for_update:
            async with self.connection.cursor() as cursor:
                await cursor.execute(
                    "SELECT id FROM content_references WHERE content_sha256 = %s FOR UPDATE",
                    (digest,),
                )
                return len(await cursor.fetchall())
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                "SELECT COUNT(*) FROM content_references WHERE content_sha256 = %s",
                (digest,),
            )
            row = await cursor.fetchone()
            return int(row[0] or 0) if row else 0

    async def get_object(
        self,
        content_sha256: str,
        *,
        for_update: bool = False,
    ) -> ContentObjectRecord | None:
        digest = _normalize_digest(content_sha256)
        suffix = " FOR UPDATE" if for_update else ""
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                """
                SELECT content_sha256, object_kind, compression, original_size_bytes,
                       stored_size_bytes, relative_path, verified_at, created_at
                FROM content_objects
                WHERE content_sha256 = %s
                """ + suffix,
                (digest,),
            )
            row = await cursor.fetchone()
            return _object_record(row) if row else None

    async def delete_metadata_if_unreferenced(
        self,
        content_sha256: str,
    ) -> ContentObjectRecord | None:
        digest = _normalize_digest(content_sha256)
        record = await self.get_object(digest, for_update=True)
        if record is None or await self.count_references(digest, for_update=True) > 0:
            return None
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                "DELETE FROM content_objects WHERE content_sha256 = %s",
                (digest,),
            )
        return record

    async def restore_object(self, record: ContentObjectRecord) -> None:
        existing = await self.get_object(record.content_sha256, for_update=True)
        async with self.connection.cursor() as cursor:
            if existing is None:
                await cursor.execute(
                    """
                    INSERT INTO content_objects (
                        content_sha256, object_kind, compression, original_size_bytes,
                        stored_size_bytes, relative_path, verified_at, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        record.content_sha256,
                        record.object_kind,
                        record.compression,
                        record.original_size_bytes,
                        record.stored_size_bytes,
                        record.relative_path,
                        record.verified_at,
                        record.created_at,
                    ),
                )
            else:
                await cursor.execute(
                    """
                    UPDATE content_objects
                    SET object_kind = %s,
                        compression = %s,
                        original_size_bytes = %s,
                        stored_size_bytes = %s,
                        relative_path = %s,
                        verified_at = %s
                    WHERE content_sha256 = %s
                    """,
                    (
                        record.object_kind,
                        record.compression,
                        record.original_size_bytes,
                        record.stored_size_bytes,
                        record.relative_path,
                        record.verified_at,
                        record.content_sha256,
                    ),
                )

    async def get_user_usage(self, user_uid: str, kinds: set[ObjectKind]) -> int:
        return await self.get_user_usage_for_reference_kinds(
            user_uid,
            reference_kinds_for_object_kinds(kinds),
        )

    async def get_user_usage_for_reference_kinds(
        self,
        user_uid: str,
        reference_kinds: tuple[str, ...] | frozenset[str],
    ) -> int:
        user_uid = _required_text(user_uid, "user_uid")
        normalized_kinds = tuple(sorted(set(reference_kinds)))
        if not normalized_kinds:
            return 0
        if any(kind not in REFERENCE_KIND_TO_OBJECT_KIND for kind in normalized_kinds):
            raise ValueError("unsupported content reference kind")
        placeholders = ",".join(["%s"] * len(normalized_kinds))
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                f"""
                SELECT COALESCE(SUM(objects.stored_size_bytes), 0)
                FROM content_objects objects
                JOIN (
                    SELECT DISTINCT content_sha256
                    FROM content_references
                    WHERE user_uid = %s AND reference_kind IN ({placeholders})
                ) refs ON refs.content_sha256 = objects.content_sha256
                """,
                (user_uid, *normalized_kinds),
            )
            row = await cursor.fetchone()
            return int(row[0] or 0) if row else 0

    async def list_body_eviction_candidates(self, user_uid: str) -> list[BodyEvictionCandidate]:
        user_uid = _required_text(user_uid, "user_uid")
        eligible = tuple(sorted(BODY_CACHE_REFERENCE_KINDS))
        placeholders = ",".join(["%s"] * len(eligible))
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                f"""
                SELECT objects.content_sha256, objects.stored_size_bytes,
                       MAX(refs.last_accessed_at) AS last_accessed_at
                FROM content_objects objects
                JOIN content_references refs
                  ON refs.content_sha256 = objects.content_sha256
                WHERE refs.user_uid = %s
                GROUP BY objects.content_sha256, objects.stored_size_bytes
                HAVING SUM(CASE WHEN refs.reference_kind IN ({placeholders}) THEN 1 ELSE 0 END) > 0
                   AND SUM(CASE WHEN refs.pinned <> 0 OR refs.reference_kind NOT IN ({placeholders}) THEN 1 ELSE 0 END) = 0
                ORDER BY last_accessed_at ASC, objects.content_sha256 ASC
                """,
                (user_uid, *eligible, *eligible),
            )
            rows = await cursor.fetchall()
        return [
            BodyEvictionCandidate(
                content_sha256=str(row[0]),
                stored_size_bytes=int(row[1] or 0),
                last_accessed_at=float(row[2] or 0),
            )
            for row in rows
        ]

    async def detach_body_digest_for_user(
        self,
        user_uid: str,
        content_sha256: str,
    ) -> DetachedBodyObject | None:
        user_uid = _required_text(user_uid, "user_uid")
        digest = _normalize_digest(content_sha256)
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                """
                SELECT id, reference_kind, reference_id, pinned
                FROM content_references
                WHERE user_uid = %s AND content_sha256 = %s
                FOR UPDATE
                """,
                (user_uid, digest),
            )
            rows = await cursor.fetchall()
            if not rows or any(
                bool(row[3]) or str(row[1]) not in BODY_CACHE_REFERENCE_KINDS
                for row in rows
            ):
                return None

            record = await self.get_object(digest, for_update=True)
            if record is None:
                return None

            direct_message_ids = {
                str(row[2])
                for row in rows
                if str(row[1]) in {"message_body_html", "message_body_text", "raw_eml"}
            }
            inline_reference_ids = [
                str(row[2]) for row in rows if str(row[1]) == "message_inline_image"
            ]
            inline_message_ids: set[str] = set()
            if inline_reference_ids:
                placeholders = ",".join(["%s"] * len(inline_reference_ids))
                await cursor.execute(
                    f"""
                    SELECT DISTINCT message_id
                    FROM message_attachments
                    WHERE user_uid = %s AND id IN ({placeholders})
                    """,
                    (user_uid, *inline_reference_ids),
                )
                inline_message_ids = {str(row[0]) for row in await cursor.fetchall()}

            await cursor.execute(
                "DELETE FROM content_references WHERE user_uid = %s AND content_sha256 = %s",
                (user_uid, digest),
            )
            removed_count = int(cursor.rowcount or 0)
            await cursor.execute(
                """
                UPDATE message_bodies SET html_object_sha256 = NULL
                WHERE user_uid = %s AND html_object_sha256 = %s
                """,
                (user_uid, digest),
            )
            await cursor.execute(
                """
                UPDATE message_bodies SET text_object_sha256 = NULL
                WHERE user_uid = %s AND text_object_sha256 = %s
                """,
                (user_uid, digest),
            )
            await cursor.execute(
                """
                UPDATE message_bodies SET raw_eml_object_sha256 = NULL
                WHERE user_uid = %s AND raw_eml_object_sha256 = %s
                """,
                (user_uid, digest),
            )
            await cursor.execute(
                """
                UPDATE message_attachments
                SET content_sha256 = NULL, cache_state = 'evicted', updated_at = %s
                WHERE user_uid = %s AND content_sha256 = %s AND is_inline = 1
                """,
                (time.time(), user_uid, digest),
            )

            message_ids = tuple(sorted(direct_message_ids | inline_message_ids))
            if message_ids:
                placeholders = ",".join(["%s"] * len(message_ids))
                await cursor.execute(
                    f"""
                    DELETE FROM body_search_documents
                    WHERE user_uid = %s AND message_id IN ({placeholders})
                    """,
                    (user_uid, *message_ids),
                )
                now = time.time()
                await cursor.execute(
                    f"""
                    UPDATE message_bodies
                    SET state = CASE
                            WHEN html_object_sha256 IS NULL
                             AND text_object_sha256 IS NULL
                             AND raw_eml_object_sha256 IS NULL
                            THEN 'evicted' ELSE state END,
                        body_size_bytes = CASE
                            WHEN html_object_sha256 IS NULL
                             AND text_object_sha256 IS NULL
                             AND raw_eml_object_sha256 IS NULL
                            THEN 0 ELSE body_size_bytes END,
                        updated_at = %s
                    WHERE user_uid = %s AND message_id IN ({placeholders})
                    """,
                    (now, user_uid, *message_ids),
                )
                await cursor.execute(
                    f"""
                    UPDATE messages AS message
                    JOIN message_bodies AS body
                      ON body.message_id = message.id
                     AND body.user_uid = message.user_uid
                    SET message.body_state = body.state,
                        message.search_state = 'evicted',
                        message.updated_at = %s
                    WHERE message.user_uid = %s
                      AND message.id IN ({placeholders})
                    """,
                    (now, user_uid, *message_ids),
                )

        return DetachedBodyObject(
            content_sha256=digest,
            logical_bytes=record.stored_size_bytes,
            message_ids=message_ids,
            removed_reference_count=removed_count,
        )

    async def list_attachment_eviction_candidates(
        self,
        user_uid: str,
    ) -> list[AttachmentEvictionCandidate]:
        user_uid = _required_text(user_uid, "user_uid")
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                """
                SELECT objects.content_sha256, objects.stored_size_bytes,
                       MAX(refs.last_accessed_at) AS last_accessed_at
                FROM content_objects objects
                JOIN content_references refs
                  ON refs.content_sha256 = objects.content_sha256
                WHERE refs.user_uid = %s
                  AND refs.reference_kind = 'message_attachment'
                GROUP BY objects.content_sha256, objects.stored_size_bytes
                HAVING SUM(refs.pinned <> 0) = 0
                ORDER BY last_accessed_at ASC, objects.content_sha256 ASC
                """,
                (user_uid,),
            )
            rows = await cursor.fetchall()
        return [
            AttachmentEvictionCandidate(
                content_sha256=str(row[0]),
                stored_size_bytes=int(row[1] or 0),
                last_accessed_at=float(row[2] or 0),
            )
            for row in rows
        ]

    async def detach_attachment_digest_for_user(
        self,
        user_uid: str,
        content_sha256: str,
    ) -> DetachedAttachmentObject | None:
        user_uid = _required_text(user_uid, "user_uid")
        digest = _normalize_digest(content_sha256)
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                """
                SELECT id, reference_id, pinned
                FROM content_references
                WHERE user_uid = %s AND content_sha256 = %s
                  AND reference_kind = 'message_attachment'
                FOR UPDATE
                """,
                (user_uid, digest),
            )
            rows = await cursor.fetchall()
            if not rows or any(bool(row[2]) for row in rows):
                return None
            record = await self.get_object(digest, for_update=True)
            if record is None:
                return None
            attachment_ids = tuple(sorted({str(row[1]) for row in rows}))
            await cursor.execute(
                """
                DELETE FROM content_references
                WHERE user_uid = %s AND content_sha256 = %s
                  AND reference_kind = 'message_attachment'
                """,
                (user_uid, digest),
            )
            removed_count = int(cursor.rowcount or 0)
            if attachment_ids:
                placeholders = ",".join("%s" for _ in attachment_ids)
                await cursor.execute(
                    f"""
                    UPDATE message_attachments
                    SET content_sha256 = NULL, cache_state = 'evicted',
                        updated_at = %s
                    WHERE user_uid = %s AND is_inline = 0
                      AND id IN ({placeholders})
                    """,
                    (time.time(), user_uid, *attachment_ids),
                )
        return DetachedAttachmentObject(
            content_sha256=digest,
            logical_bytes=record.stored_size_bytes,
            attachment_ids=attachment_ids,
            removed_reference_count=removed_count,
        )
