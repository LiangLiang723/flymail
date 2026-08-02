"""SQL-only tenant-scoped draft, recipient, attachment, and version persistence."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import aiomysql

from flymail.domain.errors import ConflictError, NotFoundError
from flymail.domain.ids import new_id
from flymail.repositories.base import TenantContext, normalize_email


@dataclass(frozen=True, slots=True)
class DraftRecord:
    id: str
    user_uid: str
    account_id: str
    identity_id: str
    thread_id: str | None
    reply_to_message_id: str | None
    subject: str
    body_html_object_sha256: str | None
    body_text_object_sha256: str | None
    version: int
    status: str
    send_state: str
    scheduled_at: float | None
    send_message_id: str
    created_at: float
    updated_at: float
    queued_at: float | None
    sent_at: float | None


def _map_draft(row: dict[str, Any]) -> DraftRecord:
    return DraftRecord(
        id=str(row["id"]),
        user_uid=str(row["user_uid"]),
        account_id=str(row["account_id"]),
        identity_id=str(row["identity_id"]),
        thread_id=str(row["thread_id"]) if row["thread_id"] else None,
        reply_to_message_id=(
            str(row["reply_to_message_id"]) if row["reply_to_message_id"] else None
        ),
        subject=str(row["subject"] or ""),
        body_html_object_sha256=(
            str(row["body_html_object_sha256"])
            if row["body_html_object_sha256"]
            else None
        ),
        body_text_object_sha256=(
            str(row["body_text_object_sha256"])
            if row["body_text_object_sha256"]
            else None
        ),
        version=max(int(row["version"] or 1), 1),
        status=str(row["status"]),
        send_state=str(row["send_state"] or "draft"),
        scheduled_at=(
            float(row["scheduled_at"]) if row["scheduled_at"] is not None else None
        ),
        send_message_id=str(row["send_message_id"] or ""),
        created_at=float(row["created_at"] or 0),
        updated_at=float(row["updated_at"] or 0),
        queued_at=float(row["queued_at"]) if row["queued_at"] is not None else None,
        sent_at=float(row["sent_at"]) if row["sent_at"] is not None else None,
    )


_DRAFT_COLUMNS = """
    id, user_uid, account_id, identity_id, thread_id, reply_to_message_id,
    subject, body_html_object_sha256, body_text_object_sha256, version,
    status, send_state, scheduled_at, send_message_id, created_at,
    updated_at, queued_at, sent_at
"""


class DraftRepository:
    def __init__(self, connection: aiomysql.Connection) -> None:
        self.connection = connection

    async def validate_identity(
        self,
        tenant: TenantContext,
        account_id: str,
        identity_id: str,
    ) -> None:
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                """
                SELECT i.id
                FROM mail_accounts a
                JOIN mail_identities i
                  ON i.account_id = a.id AND i.user_uid = a.user_uid
                WHERE a.id = %s AND a.user_uid = %s AND a.status = 'active'
                  AND i.id = %s AND i.is_verified = 1
                """,
                (
                    str(account_id or "").strip(),
                    tenant.user_uid,
                    str(identity_id or "").strip(),
                ),
            )
            row = await cursor.fetchone()
        if row is None:
            raise NotFoundError("mail account identity was not found")

    async def create(
        self,
        tenant: TenantContext,
        *,
        account_id: str,
        identity_id: str,
        thread_id: str | None,
        reply_to_message_id: str | None,
        subject: str,
        body_html_object_sha256: str | None,
        body_text_object_sha256: str | None,
        recipients: dict[str, list[dict[str, str]]],
        scheduled_at: float | None = None,
        now: float | None = None,
    ) -> DraftRecord:
        await self.validate_identity(tenant, account_id, identity_id)
        timestamp = float(time.time() if now is None else now)
        draft_id = new_id("draft")
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO drafts (
                    id, user_uid, account_id, identity_id, thread_id,
                    reply_to_message_id, subject, body_html_object_sha256,
                    body_text_object_sha256, version, status, send_state,
                    scheduled_at, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                          1, 'draft', 'draft', %s, %s, %s)
                """,
                (
                    draft_id,
                    tenant.user_uid,
                    str(account_id).strip(),
                    str(identity_id).strip(),
                    str(thread_id).strip() if thread_id else None,
                    str(reply_to_message_id).strip() if reply_to_message_id else None,
                    str(subject or ""),
                    body_html_object_sha256,
                    body_text_object_sha256,
                    scheduled_at,
                    timestamp,
                    timestamp,
                ),
            )
        await self.replace_recipients(tenant, draft_id, recipients)
        record = await self.get(tenant, draft_id)
        await self.insert_version(
            tenant,
            record,
            recipients=recipients,
            source="local",
            now=timestamp,
        )
        return record

    async def get(
        self,
        tenant: TenantContext,
        draft_id: str,
        *,
        for_update: bool = False,
    ) -> DraftRecord:
        suffix = " FOR UPDATE" if for_update else ""
        async with self.connection.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                f"""
                SELECT {_DRAFT_COLUMNS}
                FROM drafts
                WHERE id = %s AND user_uid = %s
                {suffix}
                """,
                (str(draft_id or "").strip(), tenant.user_uid),
            )
            row = await cursor.fetchone()
        if row is None:
            raise NotFoundError("draft was not found")
        return _map_draft(dict(row))

    async def list_recipients(
        self,
        tenant: TenantContext,
        draft_id: str,
    ) -> dict[str, list[dict[str, str]]]:
        result: dict[str, list[dict[str, str]]] = {"to": [], "cc": [], "bcc": []}
        async with self.connection.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                """
                SELECT recipient_kind, address, display_name
                FROM draft_recipients
                WHERE draft_id = %s AND user_uid = %s
                ORDER BY FIELD(recipient_kind, 'to', 'cc', 'bcc'),
                         position_index, id
                """,
                (str(draft_id or "").strip(), tenant.user_uid),
            )
            rows = await cursor.fetchall()
        for row in rows:
            result[str(row["recipient_kind"])].append(
                {
                    "address": str(row["address"]),
                    "display_name": str(row["display_name"] or ""),
                }
            )
        return result

    async def replace_recipients(
        self,
        tenant: TenantContext,
        draft_id: str,
        recipients: dict[str, list[dict[str, str]]],
    ) -> None:
        normalized_draft = str(draft_id or "").strip()
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                "DELETE FROM draft_recipients WHERE draft_id = %s AND user_uid = %s",
                (normalized_draft, tenant.user_uid),
            )
            rows: list[tuple[object, ...]] = []
            seen: set[str] = set()
            for kind in ("to", "cc", "bcc"):
                for index, item in enumerate(recipients.get(kind, [])):
                    address = str(item.get("address") or "").strip()
                    normalized = normalize_email(address)
                    if normalized in seen:
                        raise ConflictError("recipient addresses must be unique")
                    seen.add(normalized)
                    rows.append(
                        (
                            new_id("rcpt"),
                            normalized_draft,
                            tenant.user_uid,
                            kind,
                            address,
                            normalized,
                            str(item.get("display_name") or "").strip(),
                            index,
                        )
                    )
            if rows:
                await cursor.executemany(
                    """
                    INSERT INTO draft_recipients (
                        id, draft_id, user_uid, recipient_kind,
                        address, normalized_address, display_name,
                        position_index
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    rows,
                )

    async def update(
        self,
        tenant: TenantContext,
        draft_id: str,
        *,
        expected_version: int,
        account_id: str,
        identity_id: str,
        subject: str,
        body_html_object_sha256: str | None,
        body_text_object_sha256: str | None,
        recipients: dict[str, list[dict[str, str]]],
        scheduled_at: float | None,
        now: float | None = None,
    ) -> DraftRecord:
        current = await self.get(tenant, draft_id, for_update=True)
        if current.status not in {"draft", "failed", "review_required", "conflict"}:
            raise ConflictError("draft can no longer be edited")
        if current.version != int(expected_version):
            raise ConflictError("draft version changed")
        await self.validate_identity(tenant, account_id, identity_id)
        timestamp = float(time.time() if now is None else now)
        new_version = current.version + 1
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                """
                UPDATE drafts
                SET account_id = %s, identity_id = %s, subject = %s,
                    body_html_object_sha256 = %s,
                    body_text_object_sha256 = %s,
                    version = %s, status = 'draft', send_state = 'draft',
                    scheduled_at = %s, updated_at = %s
                WHERE id = %s AND user_uid = %s AND version = %s
                """,
                (
                    str(account_id).strip(),
                    str(identity_id).strip(),
                    str(subject or ""),
                    body_html_object_sha256,
                    body_text_object_sha256,
                    new_version,
                    scheduled_at,
                    timestamp,
                    current.id,
                    tenant.user_uid,
                    current.version,
                ),
            )
            if cursor.rowcount != 1:
                raise ConflictError("draft version changed")
        await self.replace_recipients(tenant, current.id, recipients)
        record = await self.get(tenant, current.id)
        await self.insert_version(
            tenant,
            record,
            recipients=recipients,
            source="local",
            now=timestamp,
        )
        return record

    async def insert_version(
        self,
        tenant: TenantContext,
        draft: DraftRecord,
        *,
        recipients: dict[str, list[dict[str, str]]],
        source: str,
        now: float | None = None,
    ) -> str:
        version_id = new_id("draftver")
        timestamp = float(time.time() if now is None else now)
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO draft_versions (
                    id, draft_id, user_uid, version, source, subject,
                    body_html_object_sha256, body_text_object_sha256,
                    recipients_json, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    version_id,
                    draft.id,
                    tenant.user_uid,
                    draft.version,
                    source,
                    draft.subject,
                    draft.body_html_object_sha256,
                    draft.body_text_object_sha256,
                    json.dumps(recipients, ensure_ascii=False, sort_keys=True),
                    timestamp,
                ),
            )
        return version_id

    async def latest_version_id(
        self,
        tenant: TenantContext,
        draft_id: str,
    ) -> str | None:
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                """
                SELECT id
                FROM draft_versions
                WHERE draft_id = %s AND user_uid = %s AND source = 'local'
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (str(draft_id or "").strip(), tenant.user_uid),
            )
            row = await cursor.fetchone()
        return str(row[0]) if row else None

    async def mark_conflict(self, tenant: TenantContext, draft_id: str) -> None:
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                """
                UPDATE drafts
                SET status = 'conflict', updated_at = %s
                WHERE id = %s AND user_uid = %s
                """,
                (time.time(), str(draft_id or "").strip(), tenant.user_uid),
            )
            if cursor.rowcount != 1:
                raise NotFoundError("draft was not found")

    async def insert_conflict_version(
        self,
        tenant: TenantContext,
        current: DraftRecord,
        *,
        requested_version: int,
        subject: str,
        body_html_object_sha256: str | None,
        body_text_object_sha256: str | None,
        recipients: dict[str, list[dict[str, str]]],
        now: float | None = None,
    ) -> str:
        conflict = DraftRecord(
            id=current.id,
            user_uid=current.user_uid,
            account_id=current.account_id,
            identity_id=current.identity_id,
            thread_id=current.thread_id,
            reply_to_message_id=current.reply_to_message_id,
            subject=str(subject or ""),
            body_html_object_sha256=body_html_object_sha256,
            body_text_object_sha256=body_text_object_sha256,
            version=max(int(requested_version), 1),
            status="conflict",
            send_state=current.send_state,
            scheduled_at=current.scheduled_at,
            send_message_id=current.send_message_id,
            created_at=current.created_at,
            updated_at=current.updated_at,
            queued_at=current.queued_at,
            sent_at=current.sent_at,
        )
        return await self.insert_version(
            tenant,
            conflict,
            recipients=recipients,
            source="conflict",
            now=now,
        )

    async def list_attachments(
        self,
        tenant: TenantContext,
        draft_id: str,
    ) -> list[dict[str, Any]]:
        async with self.connection.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                """
                SELECT id, content_sha256, filename, content_type,
                       size_bytes, position_index, created_at
                FROM draft_attachments
                WHERE draft_id = %s AND user_uid = %s
                ORDER BY position_index, id
                """,
                (str(draft_id or "").strip(), tenant.user_uid),
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def add_attachment(
        self,
        tenant: TenantContext,
        draft_id: str,
        *,
        attachment_id: str,
        content_sha256: str,
        filename: str,
        content_type: str,
        size_bytes: int,
        now: float | None = None,
    ) -> dict[str, Any]:
        draft = await self.get(tenant, draft_id, for_update=True)
        if draft.status != "draft":
            raise ConflictError("attachments can only be changed on editable drafts")
        timestamp = float(time.time() if now is None else now)
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                """
                SELECT COALESCE(MAX(position_index), -1) + 1
                FROM draft_attachments
                WHERE draft_id = %s AND user_uid = %s
                FOR UPDATE
                """,
                (draft.id, tenant.user_uid),
            )
            position = int((await cursor.fetchone())[0] or 0)
            await cursor.execute(
                """
                INSERT INTO draft_attachments (
                    id, draft_id, user_uid, content_sha256, filename,
                    content_type, size_bytes, position_index, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    attachment_id,
                    draft.id,
                    tenant.user_uid,
                    content_sha256,
                    str(filename or "attachment")[:1024],
                    str(content_type or "application/octet-stream")[:255],
                    max(int(size_bytes), 0),
                    position,
                    timestamp,
                ),
            )
        return (await self.list_attachments(tenant, draft.id))[-1]

    async def remove_attachment(
        self,
        tenant: TenantContext,
        draft_id: str,
        attachment_id: str,
    ) -> str:
        await self.get(tenant, draft_id, for_update=True)
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                """
                SELECT content_sha256
                FROM draft_attachments
                WHERE id = %s AND draft_id = %s AND user_uid = %s
                FOR UPDATE
                """,
                (str(attachment_id or "").strip(), str(draft_id or "").strip(), tenant.user_uid),
            )
            row = await cursor.fetchone()
            if row is None:
                raise NotFoundError("draft attachment was not found")
            await cursor.execute(
                "DELETE FROM draft_attachments WHERE id = %s AND draft_id = %s AND user_uid = %s",
                (str(attachment_id or "").strip(), str(draft_id or "").strip(), tenant.user_uid),
            )
        return str(row[0])

    async def delete(self, tenant: TenantContext, draft_id: str) -> tuple[str, ...]:
        draft = await self.get(tenant, draft_id, for_update=True)
        if draft.status in {"queued", "sending", "sent"}:
            raise ConflictError("queued or sent draft cannot be deleted")
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                """
                SELECT content_sha256 FROM draft_attachments
                WHERE draft_id = %s AND user_uid = %s
                """,
                (draft.id, tenant.user_uid),
            )
            digests = [str(row[0]) for row in await cursor.fetchall()]
            await cursor.execute(
                """
                SELECT id, body_html_object_sha256, body_text_object_sha256
                FROM draft_versions
                WHERE draft_id = %s AND user_uid = %s
                """,
                (draft.id, tenant.user_uid),
            )
            version_rows = await cursor.fetchall()
            version_ids = [str(row[0]) for row in version_rows]
            for row in version_rows:
                for digest in (row[1], row[2]):
                    if digest:
                        digests.append(str(digest))
            for digest in (
                draft.body_html_object_sha256,
                draft.body_text_object_sha256,
            ):
                if digest:
                    digests.append(digest)
            reference_ids = [draft.id, *version_ids]
            placeholders = ",".join("%s" for _ in reference_ids)
            await cursor.execute(
                f"""
                DELETE FROM content_references
                WHERE user_uid = %s
                  AND (
                      (reference_kind IN ('draft_body_html', 'draft_body_text')
                       AND reference_id IN ({placeholders}))
                      OR
                      (reference_kind = 'draft_attachment'
                       AND reference_id IN (
                           SELECT id FROM draft_attachments
                           WHERE draft_id = %s AND user_uid = %s
                       ))
                  )
                """,
                (tenant.user_uid, *reference_ids, draft.id, tenant.user_uid),
            )
            await cursor.execute(
                "DELETE FROM draft_attachments WHERE draft_id = %s AND user_uid = %s",
                (draft.id, tenant.user_uid),
            )
            await cursor.execute(
                "DELETE FROM draft_recipients WHERE draft_id = %s AND user_uid = %s",
                (draft.id, tenant.user_uid),
            )
            await cursor.execute(
                "DELETE FROM draft_versions WHERE draft_id = %s AND user_uid = %s",
                (draft.id, tenant.user_uid),
            )
            await cursor.execute(
                "DELETE FROM drafts WHERE id = %s AND user_uid = %s",
                (draft.id, tenant.user_uid),
            )
        return tuple(sorted(set(digests)))

    async def reply_source(
        self,
        tenant: TenantContext,
        message_id: str,
    ) -> dict[str, Any]:
        async with self.connection.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                """
                SELECT m.id, m.thread_id, m.subject, m.from_json, m.to_json,
                       m.cc_json, m.reply_to_json, m.snippet, m.received_at,
                       ri.account_id,
                       i.id AS identity_id, i.from_address
                FROM messages m
                JOIN message_remote_instances ri
                  ON ri.message_id = m.id AND ri.user_uid = m.user_uid
                 AND ri.remote_deleted = 0
                JOIN mail_identities i
                  ON i.account_id = ri.account_id AND i.user_uid = ri.user_uid
                 AND i.is_verified = 1
                WHERE m.id = %s AND m.user_uid = %s
                ORDER BY i.is_default DESC, ri.last_seen_at DESC, i.id
                LIMIT 1
                """,
                (str(message_id or "").strip(), tenant.user_uid),
            )
            row = await cursor.fetchone()
        if row is None:
            raise NotFoundError("reply source message was not found")
        return dict(row)

    async def storage_roots(self, tenant: TenantContext) -> list[dict[str, Any]]:
        async with self.connection.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                """
                SELECT id, label, root_path, visibility_scope
                FROM authorized_storage_roots
                WHERE enabled = 1
                  AND (visibility_scope = 'all' OR user_uid = %s)
                ORDER BY label, id
                """,
                (tenant.user_uid,),
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def storage_root(self, tenant: TenantContext, root_id: str) -> dict[str, Any]:
        async with self.connection.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                """
                SELECT id, label, root_path, visibility_scope
                FROM authorized_storage_roots
                WHERE id = %s AND enabled = 1
                  AND (visibility_scope = 'all' OR user_uid = %s)
                """,
                (str(root_id or "").strip(), tenant.user_uid),
            )
            row = await cursor.fetchone()
        if row is None:
            raise NotFoundError("authorized storage root was not found")
        return dict(row)
