"""Versioned draft, attachment import, reply template, and send orchestration."""

from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import stat
import time
from pathlib import Path, PurePosixPath
from typing import AsyncIterable, AsyncIterator

import aiomysql

from flymail.api.schemas.compose import (
    ComposeTemplateResponse,
    DraftAttachmentResponse,
    DraftResponse,
    Recipient,
    RecipientGroups,
    StorageRootResponse,
)
from flymail.application.auth import AuthenticatedSession
from flymail.domain.enums import ObjectKind
from flymail.domain.errors import ApiContractError, ConflictError, NotFoundError
from flymail.domain.ids import new_id
from flymail.infrastructure.db.pool import DatabasePool
from flymail.infrastructure.object_store.store import ObjectStore
from flymail.providers.registry import ProviderRegistry
from flymail.repositories.base import TenantContext
from flymail.repositories.drafts import DraftRecord, DraftRepository
from flymail.repositories.objects import ObjectRepository
from flymail.workers.sender import QueuedSend, SendService


_MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024


async def _one_chunk(value: bytes):
    if value:
        yield value


class ComposeService:
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
        self.sender = SendService(pool, store, ProviderRegistry.default())

    @staticmethod
    def _recipient_dict(groups: RecipientGroups) -> dict[str, list[dict[str, str]]]:
        return {
            kind: [item.model_dump(mode="json") for item in getattr(groups, kind)]
            for kind in ("to", "cc", "bcc")
        }

    @staticmethod
    def _recipient_groups(value: dict[str, list[dict[str, str]]]) -> RecipientGroups:
        return RecipientGroups(
            to=tuple(Recipient(**item) for item in value.get("to", [])),
            cc=tuple(Recipient(**item) for item in value.get("cc", [])),
            bcc=tuple(Recipient(**item) for item in value.get("bcc", [])),
        )

    async def _store_body(self, value: str, kind: ObjectKind):
        encoded = str(value or "").encode("utf-8")
        if not encoded:
            return None
        return await self.store.put_stream(kind, _one_chunk(encoded), expected_size=len(encoded))

    async def _read_text(self, digest: str | None) -> str:
        if not digest:
            return ""
        async with self.store.open(digest) as handle:
            return (await asyncio.to_thread(handle.read)).decode("utf-8", errors="replace")

    async def _attach_body(
        self,
        connection: aiomysql.Connection,
        tenant: TenantContext,
        stored,
        *,
        reference_kind: str,
        reference_id: str,
    ) -> None:
        if stored is None:
            return
        await ObjectRepository(connection).attach_reference(
            stored,
            user_uid=tenant.user_uid,
            reference_kind=reference_kind,
            reference_id=reference_id,
            pinned=True,
            last_accessed_at=float(self.now_fn()),
        )

    async def _cleanup(self, digests: tuple[str, ...] | list[str]) -> None:
        for digest in sorted(set(value for value in digests if value)):
            async with self.pool.acquire() as connection:
                await self.store.remove_unreferenced(
                    digest,
                    ObjectRepository(connection),
                )

    async def _validate_optional_scope(
        self,
        connection: aiomysql.Connection,
        tenant: TenantContext,
        *,
        thread_id: str | None,
        reply_to_message_id: str | None,
    ) -> None:
        if thread_id:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "SELECT id FROM threads WHERE id=%s AND user_uid=%s",
                    (str(thread_id).strip(), tenant.user_uid),
                )
                if await cursor.fetchone() is None:
                    raise NotFoundError("thread was not found")
        if reply_to_message_id:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "SELECT id FROM messages WHERE id=%s AND user_uid=%s",
                    (str(reply_to_message_id).strip(), tenant.user_uid),
                )
                if await cursor.fetchone() is None:
                    raise NotFoundError("reply message was not found")

    async def create_draft(
        self,
        session: AuthenticatedSession,
        *,
        account_id: str,
        identity_id: str,
        thread_id: str | None,
        reply_to_message_id: str | None,
        subject: str,
        body_html: str,
        body_text: str,
        recipients: RecipientGroups,
        scheduled_at: float | None,
    ) -> DraftResponse:
        tenant = TenantContext(session.user.id)
        html = await self._store_body(body_html, ObjectKind.BODY_HTML)
        text = await self._store_body(body_text, ObjectKind.BODY_TEXT)
        digests = [item.content_sha256 for item in (html, text) if item is not None]
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                await self._validate_optional_scope(
                    connection,
                    tenant,
                    thread_id=thread_id,
                    reply_to_message_id=reply_to_message_id,
                )
                repository = DraftRepository(connection)
                record = await repository.create(
                    tenant,
                    account_id=account_id,
                    identity_id=identity_id,
                    thread_id=thread_id,
                    reply_to_message_id=reply_to_message_id,
                    subject=subject,
                    body_html_object_sha256=html.content_sha256 if html else None,
                    body_text_object_sha256=text.content_sha256 if text else None,
                    recipients=self._recipient_dict(recipients),
                    scheduled_at=scheduled_at,
                    now=float(self.now_fn()),
                )
                await self._attach_body(
                    connection,
                    tenant,
                    html,
                    reference_kind="draft_body_html",
                    reference_id=record.id,
                )
                await self._attach_body(
                    connection,
                    tenant,
                    text,
                    reference_kind="draft_body_text",
                    reference_id=record.id,
                )
                await connection.commit()
            except Exception:
                await connection.rollback()
                await self._cleanup(digests)
                raise
        return await self.get_draft(session, record.id)

    async def get_draft(
        self,
        session: AuthenticatedSession,
        draft_id: str,
    ) -> DraftResponse:
        tenant = TenantContext(session.user.id)
        async with self.pool.acquire() as connection:
            repository = DraftRepository(connection)
            record = await repository.get(tenant, draft_id)
            recipients = await repository.list_recipients(tenant, record.id)
            attachments = await repository.list_attachments(tenant, record.id)
        return DraftResponse(
            id=record.id,
            account_id=record.account_id,
            identity_id=record.identity_id,
            thread_id=record.thread_id,
            reply_to_message_id=record.reply_to_message_id,
            subject=record.subject,
            body_html=await self._read_text(record.body_html_object_sha256),
            body_text=await self._read_text(record.body_text_object_sha256),
            recipients=self._recipient_groups(recipients),
            attachments=tuple(self._attachment(item) for item in attachments),
            version=record.version,
            status=record.status,
            send_state=record.send_state,
            scheduled_at=record.scheduled_at,
            send_message_id=record.send_message_id,
            created_at=record.created_at,
            updated_at=record.updated_at,
            queued_at=record.queued_at,
            sent_at=record.sent_at,
        )

    @staticmethod
    def _attachment(row: dict) -> DraftAttachmentResponse:
        return DraftAttachmentResponse(
            id=str(row["id"]),
            filename=str(row["filename"] or ""),
            content_type=str(row["content_type"] or "application/octet-stream"),
            size_bytes=max(int(row["size_bytes"] or 0), 0),
            position_index=max(int(row["position_index"] or 0), 0),
            created_at=float(row["created_at"] or 0),
        )

    async def update_draft(
        self,
        session: AuthenticatedSession,
        draft_id: str,
        *,
        expected_version: int,
        account_id: str,
        identity_id: str,
        subject: str,
        body_html: str,
        body_text: str,
        recipients: RecipientGroups,
        scheduled_at: float | None,
    ) -> DraftResponse:
        tenant = TenantContext(session.user.id)
        html = await self._store_body(body_html, ObjectKind.BODY_HTML)
        text = await self._store_body(body_text, ObjectKind.BODY_TEXT)
        digests = [item.content_sha256 for item in (html, text) if item is not None]
        conflict_details: dict | None = None
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                repository = DraftRepository(connection)
                current = await repository.get(tenant, draft_id, for_update=True)
                recipient_value = self._recipient_dict(recipients)
                if current.version != int(expected_version):
                    incoming_version_id = await repository.insert_conflict_version(
                        tenant,
                        current,
                        requested_version=expected_version,
                        subject=subject,
                        body_html_object_sha256=html.content_sha256 if html else None,
                        body_text_object_sha256=text.content_sha256 if text else None,
                        recipients=recipient_value,
                        now=float(self.now_fn()),
                    )
                    await self._attach_body(
                        connection,
                        tenant,
                        html,
                        reference_kind="draft_body_html",
                        reference_id=incoming_version_id,
                    )
                    await self._attach_body(
                        connection,
                        tenant,
                        text,
                        reference_kind="draft_body_text",
                        reference_id=incoming_version_id,
                    )
                    current_version_id = await repository.latest_version_id(tenant, current.id)
                    await repository.mark_conflict(tenant, current.id)
                    conflict_details = {
                        "current_version": current.version,
                        "current_version_id": current_version_id,
                        "incoming_version_id": incoming_version_id,
                    }
                else:
                    record = await repository.update(
                        tenant,
                        current.id,
                        expected_version=expected_version,
                        account_id=account_id,
                        identity_id=identity_id,
                        subject=subject,
                        body_html_object_sha256=html.content_sha256 if html else None,
                        body_text_object_sha256=text.content_sha256 if text else None,
                        recipients=recipient_value,
                        scheduled_at=scheduled_at,
                        now=float(self.now_fn()),
                    )
                    await self._attach_body(
                        connection,
                        tenant,
                        html,
                        reference_kind="draft_body_html",
                        reference_id=record.id,
                    )
                    await self._attach_body(
                        connection,
                        tenant,
                        text,
                        reference_kind="draft_body_text",
                        reference_id=record.id,
                    )
                await connection.commit()
            except Exception:
                await connection.rollback()
                await self._cleanup(digests)
                raise
        if conflict_details is not None:
            raise ApiContractError(
                "draft_version_conflict",
                "草稿已在其他位置更新，已保留冲突版本",
                status_code=409,
                details=conflict_details,
            )
        return await self.get_draft(session, draft_id)

    async def delete_draft(self, session: AuthenticatedSession, draft_id: str) -> None:
        tenant = TenantContext(session.user.id)
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                digests = await DraftRepository(connection).delete(tenant, draft_id)
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
        await self._cleanup(digests)

    async def _quota_allows(
        self,
        connection: aiomysql.Connection,
        tenant: TenantContext,
        size_bytes: int,
    ) -> None:
        async with connection.cursor() as cursor:
            await cursor.execute(
                """
                SELECT s.attachment_cache_quota_bytes,
                       COALESCE(SUM(a.size_bytes), 0)
                FROM user_settings s
                LEFT JOIN draft_attachments a ON a.user_uid = s.user_uid
                WHERE s.user_uid = %s
                GROUP BY s.attachment_cache_quota_bytes
                """,
                (tenant.user_uid,),
            )
            row = await cursor.fetchone()
        quota = int(row[0] or 0) if row else 0
        used = int(row[1] or 0) if row else 0
        if quota > 0 and used + int(size_bytes) > quota:
            raise ApiContractError(
                "attachment_quota_exceeded",
                "附件缓存配额不足",
                status_code=413,
                details={"quota_bytes": quota, "used_bytes": used},
            )

    async def add_attachment(
        self,
        session: AuthenticatedSession,
        draft_id: str,
        *,
        filename: str,
        content_type: str,
        chunks: AsyncIterable[bytes],
        expected_size: int | None = None,
    ) -> DraftAttachmentResponse:
        if expected_size is not None and int(expected_size) > _MAX_ATTACHMENT_BYTES:
            raise ApiContractError("attachment_too_large", "附件超过大小限制", status_code=413)
        total = 0

        async def bounded() -> AsyncIterator[bytes]:
            nonlocal total
            async for chunk in chunks:
                total += len(chunk)
                if total > _MAX_ATTACHMENT_BYTES:
                    raise ApiContractError(
                        "attachment_too_large",
                        "附件超过大小限制",
                        status_code=413,
                    )
                yield chunk

        stored = await self.store.put_stream(
            ObjectKind.DRAFT_ATTACHMENT,
            bounded(),
            expected_size=expected_size,
        )
        tenant = TenantContext(session.user.id)
        attachment_id = new_id("draftatt")
        try:
            async with self.pool.acquire() as connection:
                await connection.begin()
                try:
                    await self._quota_allows(connection, tenant, stored.original_size_bytes)
                    repository = DraftRepository(connection)
                    row = await repository.add_attachment(
                        tenant,
                        draft_id,
                        attachment_id=attachment_id,
                        content_sha256=stored.content_sha256,
                        filename=Path(str(filename or "attachment")).name,
                        content_type=content_type,
                        size_bytes=stored.original_size_bytes,
                        now=float(self.now_fn()),
                    )
                    await ObjectRepository(connection).attach_reference(
                        stored,
                        user_uid=tenant.user_uid,
                        reference_kind="draft_attachment",
                        reference_id=attachment_id,
                        pinned=True,
                        last_accessed_at=float(self.now_fn()),
                    )
                    await connection.commit()
                except Exception:
                    await connection.rollback()
                    raise
        except Exception:
            await self._cleanup([stored.content_sha256])
            raise
        return self._attachment(row)

    async def remove_attachment(
        self,
        session: AuthenticatedSession,
        draft_id: str,
        attachment_id: str,
    ) -> None:
        tenant = TenantContext(session.user.id)
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                repository = DraftRepository(connection)
                digest = await repository.remove_attachment(tenant, draft_id, attachment_id)
                await ObjectRepository(connection).detach_reference(
                    user_uid=tenant.user_uid,
                    reference_kind="draft_attachment",
                    reference_id=attachment_id,
                )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
        await self._cleanup([digest])

    async def storage_roots(
        self,
        session: AuthenticatedSession,
    ) -> tuple[StorageRootResponse, ...]:
        tenant = TenantContext(session.user.id)
        async with self.pool.acquire() as connection:
            rows = await DraftRepository(connection).storage_roots(tenant)
        return tuple(
            StorageRootResponse(
                id=str(row["id"]),
                label=str(row["label"]),
                visibility_scope=str(row["visibility_scope"]),
            )
            for row in rows
        )

    @staticmethod
    def _safe_import_path(root: Path, relative_path: str) -> Path:
        relative = PurePosixPath(str(relative_path or "").replace("\\", "/"))
        if relative.is_absolute() or not relative.parts or any(
            part in {"", ".", ".."} for part in relative.parts
        ):
            raise ApiContractError("unsafe_storage_path", "服务器文件路径无效", status_code=403)
        root = Path(root)
        root_stat = os.lstat(root)
        if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
            raise ApiContractError("unsafe_storage_path", "授权存储根无效", status_code=403)
        current = root
        for index, part in enumerate(relative.parts):
            current = current / part
            try:
                item_stat = os.lstat(current)
            except FileNotFoundError:
                raise NotFoundError("server attachment file was not found") from None
            if stat.S_ISLNK(item_stat.st_mode):
                raise ApiContractError("unsafe_storage_path", "不允许导入符号链接", status_code=403)
            if index < len(relative.parts) - 1 and not stat.S_ISDIR(item_stat.st_mode):
                raise ApiContractError("unsafe_storage_path", "服务器文件路径无效", status_code=403)
        final_stat = os.lstat(current)
        if not stat.S_ISREG(final_stat.st_mode):
            raise ApiContractError("unsafe_storage_path", "只允许导入普通文件", status_code=403)
        if final_stat.st_size > _MAX_ATTACHMENT_BYTES:
            raise ApiContractError("attachment_too_large", "附件超过大小限制", status_code=413)
        resolved_root = root.resolve(strict=True)
        resolved_file = current.resolve(strict=True)
        if not resolved_file.is_relative_to(resolved_root):
            raise ApiContractError("unsafe_storage_path", "服务器文件越过授权根", status_code=403)
        return current

    async def import_attachment(
        self,
        session: AuthenticatedSession,
        draft_id: str,
        *,
        root_id: str,
        relative_path: str,
    ) -> DraftAttachmentResponse:
        tenant = TenantContext(session.user.id)
        async with self.pool.acquire() as connection:
            row = await DraftRepository(connection).storage_root(tenant, root_id)
        path = self._safe_import_path(Path(str(row["root_path"])), relative_path)
        size = path.stat().st_size

        async def file_chunks() -> AsyncIterator[bytes]:
            with path.open("rb") as handle:
                while True:
                    chunk = await asyncio.to_thread(handle.read, 64 * 1024)
                    if not chunk:
                        break
                    yield chunk

        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return await self.add_attachment(
            session,
            draft_id,
            filename=path.name,
            content_type=content_type,
            chunks=file_chunks(),
            expected_size=size,
        )

    @staticmethod
    def _array(value: object) -> list[str]:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return []
        return [str(item) for item in value] if isinstance(value, list) else []

    async def compose_template(
        self,
        session: AuthenticatedSession,
        message_id: str,
        *,
        mode: str,
    ) -> ComposeTemplateResponse:
        tenant = TenantContext(session.user.id)
        async with self.pool.acquire() as connection:
            source = await DraftRepository(connection).reply_source(tenant, message_id)
        normalized_mode = str(mode or "reply").casefold()
        if normalized_mode not in {"reply", "forward"}:
            raise ApiContractError("validation_error", "撰写模板模式无效", status_code=422)
        subject = str(source["subject"] or "")
        prefix = "Re:" if normalized_mode == "reply" else "Fwd:"
        if not subject.casefold().startswith(prefix.casefold()):
            subject = f"{prefix} {subject}".strip()
        recipients = RecipientGroups()
        if normalized_mode == "reply":
            reply_to = self._array(source["reply_to_json"])
            from_addresses = self._array(source["from_json"])
            target = (reply_to or from_addresses or [""])[0]
            recipients = RecipientGroups(to=(Recipient(address=target),))
        original = str(source.get("snippet") or "")
        return ComposeTemplateResponse(
            account_id=str(source["account_id"]),
            identity_id=str(source["identity_id"]),
            thread_id=str(source["thread_id"]) if source["thread_id"] else None,
            reply_to_message_id=str(source["id"]),
            subject=subject,
            body_html=f"<blockquote>{original}</blockquote>" if original else "",
            body_text=f"> {original}" if original else "",
            recipients=recipients,
        )

    async def queue_send(
        self,
        session: AuthenticatedSession,
        draft_id: str,
        *,
        idempotency_key: str,
    ) -> QueuedSend:
        return await self.sender.queue_draft(
            TenantContext(session.user.id),
            draft_id,
            idempotency_key=idempotency_key,
            now=float(self.now_fn()),
        )

    async def cancel_send(
        self,
        session: AuthenticatedSession,
        draft_id: str,
        *,
        operation_id: str,
    ) -> None:
        await self.sender.cancel(
            TenantContext(session.user.id),
            draft_id,
            operation_id=operation_id,
            now=float(self.now_fn()),
        )
