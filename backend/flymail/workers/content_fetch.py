"""Layered, exact-part message content fetching for FlyMail V2."""

from __future__ import annotations

import asyncio
import base64
import binascii
import gzip
import html
import quopri
import re
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, replace
from html.parser import HTMLParser
from typing import AsyncIterable, Protocol

import aiomysql

from flymail.infrastructure.db.uow import SqlUnitOfWork
from flymail.domain.enums import ObjectKind
from flymail.domain.errors import ConflictError, NotFoundError, PermanentError
from flymail.domain.ids import new_id
from flymail.domain.mail import MimePart, MimeTree
from flymail.infrastructure.db.pool import DatabasePool
from flymail.infrastructure.object_store.models import StoredObject
from flymail.infrastructure.object_store.store import ObjectStore
from flymail.providers.core.mime_parts import build_partial_fetch, select_message_parts, validate_imap_part
from flymail.repositories.base import TenantContext, fetch_all, fetch_one
from flymail.repositories.jobs import JobRepository, JobSpec
from flymail.repositories.messages import MessageRepository
from flymail.repositories.objects import ObjectRepository


_ALLOWED_BODY_TRANSITIONS = {
    "not_requested": {"queued"},
    "queued": {"fetching", "failed"},
    "fetching": {"ready", "failed", "unavailable"},
    "ready": {"evicted"},
    "evicted": {"queued"},
    "failed": {"queued"},
    "unavailable": set(),
}
_ALLOWED_ATTACHMENT_TRANSITIONS = dict(_ALLOWED_BODY_TRANSITIONS)
_CID_PATTERN = re.compile(r"^cid\s*:\s*<?([^<>\s]+)>?$", re.IGNORECASE)
_SAFE_LINK_SCHEMES = ("http://", "https://", "mailto:")
_ALLOWED_TAGS = {
    "html", "body", "p", "div", "span", "br", "b", "strong", "i", "em", "u",
    "s", "ul", "ol", "li", "blockquote", "pre", "code", "table", "thead",
    "tbody", "tfoot", "tr", "td", "th", "hr", "a", "img",
}
_VOID_TAGS = {"br", "hr", "img"}
_BLOCKED_TAGS = {"script", "style", "iframe", "object", "embed", "form", "input", "button"}


@dataclass(frozen=True, slots=True)
class RemoteContentLocator:
    remote_instance_id: str
    account_id: str
    provider_key: str
    mailbox_native_key: str
    uidvalidity: int
    remote_uid: int


@dataclass(frozen=True, slots=True)
class StructureResult:
    body_parts: int
    inline_parts: int
    ordinary_attachments: int


@dataclass(frozen=True, slots=True)
class ContentFetchResult:
    state: str
    content_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class _BodyPartRecord:
    body_kind: str
    imap_part: str
    content_type: str
    charset: str
    transfer_encoding: str
    remote_size_bytes: int


@dataclass(frozen=True, slots=True)
class _AttachmentRecord:
    id: str
    message_id: str
    remote_instance_id: str
    imap_part: str
    content_type: str
    content_id: str
    transfer_encoding: str
    remote_size_bytes: int
    is_inline: bool
    is_referenced_inline: bool
    cache_state: str
    locator: RemoteContentLocator


class ContentTransport(Protocol):
    def stream(
        self,
        locator: RemoteContentLocator,
        fetch_spec: str,
    ) -> AsyncIterable[bytes]: ...


class _Sanitizer(HTMLParser):
    def __init__(self, cid_targets: dict[str, tuple[str, str]]) -> None:
        super().__init__(convert_charrefs=True)
        self.cid_targets = cid_targets
        self.output: list[str] = []
        self.referenced_attachment_ids: set[str] = set()
        self._blocked_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.casefold()
        if normalized_tag in _BLOCKED_TAGS:
            self._blocked_depth += 1
            return
        if self._blocked_depth or normalized_tag not in _ALLOWED_TAGS:
            return
        attributes = {str(key).casefold(): str(value or "") for key, value in attrs}
        rendered: list[tuple[str, str]] = []
        if normalized_tag == "img":
            source = attributes.get("src", "").strip()
            match = _CID_PATTERN.fullmatch(source)
            if not match:
                return
            normalized_cid = match.group(1).strip().strip("<>").casefold()
            target = self.cid_targets.get(normalized_cid)
            if target is None:
                return
            attachment_id, local_path = target
            self.referenced_attachment_ids.add(attachment_id)
            rendered.append(("src", local_path))
            if attributes.get("alt"):
                rendered.append(("alt", attributes["alt"][:512]))
        elif normalized_tag == "a":
            href = attributes.get("href", "").strip()
            if href.casefold().startswith(_SAFE_LINK_SCHEMES):
                rendered.append(("href", href[:4096]))
                rendered.append(("rel", "noopener noreferrer"))
        else:
            for key in ("colspan", "rowspan", "title", "alt"):
                value = attributes.get(key, "").strip()
                if value:
                    rendered.append((key, value[:512]))
        suffix = "".join(
            f' {key}="{html.escape(value, quote=True)}"' for key, value in rendered
        )
        self.output.append(f"<{normalized_tag}{suffix}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        before = len(self.output)
        self.handle_starttag(tag, attrs)
        if len(self.output) > before and tag.casefold() not in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.casefold()
        if normalized_tag in _BLOCKED_TAGS:
            self._blocked_depth = max(0, self._blocked_depth - 1)
            return
        if self._blocked_depth or normalized_tag not in _ALLOWED_TAGS or normalized_tag in _VOID_TAGS:
            return
        self.output.append(f"</{normalized_tag}>")

    def handle_data(self, data: str) -> None:
        if not self._blocked_depth:
            self.output.append(html.escape(data, quote=False))

    def handle_entityref(self, name: str) -> None:
        if not self._blocked_depth:
            self.output.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if not self._blocked_depth:
            self.output.append(f"&#{name};")


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.fragments: list[str] = []

    def handle_data(self, data: str) -> None:
        value = data.strip()
        if value:
            self.fragments.append(value)


class ContentJobPublisher:
    def __init__(self, pool: DatabasePool) -> None:
        self.pool = pool

    async def enqueue(
        self,
        connection: aiomysql.Connection,
        tenant: TenantContext,
        *,
        locator: RemoteContentLocator,
        job_kind: str,
        reference_id: str,
        payload: dict,
        now: float,
    ) -> str:
        return await JobRepository(connection).enqueue(
            JobSpec(
                queue_name="interactive",
                job_kind=job_kind,
                payload=dict(payload),
                user_uid=tenant.user_uid,
                account_id=locator.account_id,
                provider_key=locator.provider_key,
                priority=10,
                available_at=float(now),
                max_attempts=10,
                dedupe_key=f"{job_kind}:{tenant.user_uid}:{reference_id}",
            ),
            now=float(now),
        )


class ContentFetchService:
    def __init__(
        self,
        pool: DatabasePool,
        store: ObjectStore,
        transport: ContentTransport,
        publisher: ContentJobPublisher,
        *,
        body_limit_bytes: int,
        attachment_limit_bytes: int,
        partial_chunk_bytes: int,
    ) -> None:
        for name, value in (
            ("body_limit_bytes", body_limit_bytes),
            ("attachment_limit_bytes", attachment_limit_bytes),
            ("partial_chunk_bytes", partial_chunk_bytes),
        ):
            if isinstance(value, bool) or int(value) < 1:
                raise ValueError(f"{name} must be at least 1")
        self.pool = pool
        self.store = store
        self.transport = transport
        self.publisher = publisher
        self.body_limit_bytes = int(body_limit_bytes)
        self.attachment_limit_bytes = int(attachment_limit_bytes)
        self.partial_chunk_bytes = int(partial_chunk_bytes)

    async def record_structure(
        self,
        tenant: TenantContext,
        *,
        message_id: str,
        remote_instance_id: str,
        tree: MimeTree,
        now: float | None = None,
    ) -> StructureResult:
        timestamp = float(time.time() if now is None else now)
        selection = select_message_parts(tree)
        body_parts = tuple(
            (kind, part)
            for kind, part in (("text", selection.text_part), ("html", selection.html_part))
            if part is not None
        )
        attachment_parts = tuple(selection.inline_candidates) + tuple(selection.attachment_parts)
        async with SqlUnitOfWork(self.pool) as uow:
            connection = self._connection(uow)
            await self._require_locator(
                connection,
                tenant,
                message_id=message_id,
                remote_instance_id=remote_instance_id,
                for_update=True,
            )
            await self._ensure_body_row(connection, tenant, message_id, timestamp)
            await self._upsert_body_parts(
                connection,
                tenant,
                message_id,
                remote_instance_id,
                body_parts,
                timestamp,
            )
            await self._upsert_attachments(
                connection,
                tenant,
                message_id,
                remote_instance_id,
                attachment_parts,
                {part.imap_part for part in selection.inline_candidates},
                timestamp,
            )
            await uow.commit()
        return StructureResult(
            body_parts=len(body_parts),
            inline_parts=len(selection.inline_candidates),
            ordinary_attachments=len(selection.attachment_parts),
        )

    async def request_body(
        self,
        tenant: TenantContext,
        message_id: str,
        *,
        now: float | None = None,
    ) -> str:
        timestamp = float(time.time() if now is None else now)
        async with SqlUnitOfWork(self.pool) as uow:
            connection = self._connection(uow)
            locator = await self._message_locator(connection, tenant, message_id, for_update=True)
            body = await self._body_row(connection, tenant, message_id, for_update=True)
            state = str(body["state"])
            if state in {"not_requested", "evicted", "failed"}:
                await MessageRepository(connection).transition_body_state(
                    tenant, message_id, "queued", now=timestamp
                )
            elif state == "queued":
                pass
            elif state == "unavailable":
                raise ConflictError("message body is unavailable")
            else:
                raise ConflictError(f"message body is already {state}")
            job_id = await self.publisher.enqueue(
                connection,
                tenant,
                locator=locator,
                job_kind="content.body",
                reference_id=message_id,
                payload={"message_id": message_id},
                now=timestamp,
            )
            await uow.commit()
            return job_id

    async def request_attachment(
        self,
        tenant: TenantContext,
        attachment_id: str,
        *,
        now: float | None = None,
    ) -> str:
        return await self._request_attachment_kind(
            tenant,
            attachment_id,
            job_kind="content.attachment",
            require_inline=False,
            now=now,
        )

    async def request_raw_eml(
        self,
        tenant: TenantContext,
        message_id: str,
        *,
        now: float | None = None,
    ) -> str:
        timestamp = float(time.time() if now is None else now)
        async with SqlUnitOfWork(self.pool) as uow:
            connection = self._connection(uow)
            locator = await self._message_locator(connection, tenant, message_id, for_update=True)
            await self._body_row(connection, tenant, message_id, for_update=True)
            job_id = await self.publisher.enqueue(
                connection,
                tenant,
                locator=locator,
                job_kind="content.raw_eml",
                reference_id=message_id,
                payload={"message_id": message_id},
                now=timestamp,
            )
            await uow.commit()
            return job_id

    async def fetch_body(
        self,
        tenant: TenantContext,
        message_id: str,
        *,
        now: float | None = None,
    ) -> ContentFetchResult:
        timestamp = float(time.time() if now is None else now)
        locator, parts, cid_targets = await self._begin_body_fetch(
            tenant, message_id, timestamp
        )
        try:
            if any(part.remote_size_bytes > self.body_limit_bytes for part in parts):
                raise PermanentError("message body part exceeds configured limit")
            decoded: dict[str, bytes] = {}
            for part in parts:
                raw = await self._collect(
                    self.transport.stream(locator, f"BODY.PEEK[{validate_imap_part(part.imap_part)}]"),
                    self.body_limit_bytes * 2,
                )
                value = self._decode_transfer(raw, part.transfer_encoding)
                if len(value) > self.body_limit_bytes:
                    raise PermanentError("decoded body part exceeds configured limit")
                decoded[part.body_kind] = value

            text_value = self._decode_charset(
                decoded.get("text", b""),
                next((part.charset for part in parts if part.body_kind == "text"), ""),
            ) if "text" in decoded else ""
            html_value = self._decode_charset(
                decoded.get("html", b""),
                next((part.charset for part in parts if part.body_kind == "html"), ""),
            ) if "html" in decoded else ""
            sanitized_html, referenced_ids = self._sanitize_html(html_value, cid_targets)
            if not text_value and sanitized_html:
                text_value = self._html_text(sanitized_html)

            stored: dict[str, StoredObject] = {}
            if text_value:
                stored["text"] = await self._store_text(ObjectKind.BODY_TEXT, text_value)
            if sanitized_html:
                stored["html"] = await self._store_text(ObjectKind.BODY_HTML, sanitized_html)
            if not stored:
                raise PermanentError("message has no displayable body part")
            await self._finish_body_fetch(
                tenant,
                message_id,
                stored,
                text_value,
                referenced_ids,
                timestamp,
            )
            return ContentFetchResult(state="ready")
        except BaseException as exc:
            await self._mark_body_failure(tenant, message_id, exc, timestamp)
            raise

    async def fetch_inline(
        self,
        tenant: TenantContext,
        attachment_id: str,
        *,
        now: float | None = None,
    ) -> ContentFetchResult:
        return await self._fetch_attachment_bytes(
            tenant,
            attachment_id,
            object_kind=ObjectKind.INLINE_IMAGE,
            reference_kind="message_inline_image",
            require_inline=True,
            supports_partial=False,
            now=now,
        )

    async def fetch_attachment(
        self,
        tenant: TenantContext,
        attachment_id: str,
        *,
        supports_partial: bool,
        now: float | None = None,
    ) -> ContentFetchResult:
        if not isinstance(supports_partial, bool):
            raise TypeError("supports_partial must be bool")
        return await self._fetch_attachment_bytes(
            tenant,
            attachment_id,
            object_kind=ObjectKind.ATTACHMENT,
            reference_kind="message_attachment",
            require_inline=False,
            supports_partial=supports_partial,
            now=now,
        )

    async def fetch_raw_eml(
        self,
        tenant: TenantContext,
        message_id: str,
        *,
        now: float | None = None,
    ) -> ContentFetchResult:
        timestamp = float(time.time() if now is None else now)
        async with self.pool.acquire() as connection:
            locator = await self._message_locator(connection, tenant, message_id)
        raw = await self._collect(
            self.transport.stream(locator, "BODY.PEEK[]"),
            max(self.body_limit_bytes, self.attachment_limit_bytes),
        )
        stored = await self._store_bytes(ObjectKind.RAW_EML, raw, compress=True)
        async with SqlUnitOfWork(self.pool) as uow:
            connection = self._connection(uow)
            body = await self._body_row(connection, tenant, message_id, for_update=True)
            old_digest = str(body.get("raw_eml_object_sha256") or "")
            old_size = 0
            if old_digest:
                old_object = await fetch_one(
                    connection,
                    """
                    SELECT original_size_bytes
                    FROM content_objects
                    WHERE content_sha256 = %s
                    """,
                    (old_digest,),
                )
                old_size = int(old_object["original_size_bytes"] or 0) if old_object else 0
            repository = ObjectRepository(connection)
            async with AsyncExitStack() as locks:
                for digest in sorted({value for value in (old_digest, stored.content_sha256) if value}):
                    await locks.enter_async_context(repository.lock_object(digest))
                if old_digest and old_digest != stored.content_sha256:
                    await repository.detach_reference(
                        user_uid=tenant.user_uid,
                        reference_kind="raw_eml",
                        reference_id=message_id,
                    )
                await repository.attach_reference(
                    stored,
                    user_uid=tenant.user_uid,
                    reference_kind="raw_eml",
                    reference_id=message_id,
                    last_accessed_at=timestamp,
                )
                body_size = max(int(body["body_size_bytes"] or 0) - old_size, 0)
                body_size += stored.original_size_bytes
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        UPDATE message_bodies
                        SET raw_eml_object_sha256 = %s,
                            body_size_bytes = %s,
                            cached_at = COALESCE(cached_at, %s),
                            last_accessed_at = %s, updated_at = %s
                        WHERE user_uid = %s AND message_id = %s
                        """,
                        (
                            stored.content_sha256,
                            body_size,
                            timestamp,
                            timestamp,
                            timestamp,
                            tenant.user_uid,
                            message_id,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise NotFoundError("message body was not found")
                await uow.commit()
        return ContentFetchResult(state="ready", content_sha256=stored.content_sha256)

    async def _begin_body_fetch(
        self,
        tenant: TenantContext,
        message_id: str,
        timestamp: float,
    ) -> tuple[RemoteContentLocator, tuple[_BodyPartRecord, ...], dict[str, tuple[str, str]]]:
        async with SqlUnitOfWork(self.pool) as uow:
            connection = self._connection(uow)
            locator = await self._message_locator(connection, tenant, message_id, for_update=True)
            body = await self._body_row(connection, tenant, message_id, for_update=True)
            if str(body["state"]) != "queued":
                raise ConflictError("message body is not queued for fetching")
            await MessageRepository(connection).transition_body_state(
                tenant, message_id, "fetching", now=timestamp
            )
            rows = await fetch_all(
                connection,
                """
                SELECT body_kind, imap_part, content_type, charset,
                       transfer_encoding, remote_size_bytes
                FROM message_body_parts
                WHERE user_uid = %s AND message_id = %s
                  AND remote_instance_id = %s
                ORDER BY FIELD(body_kind, 'text', 'html')
                """,
                (tenant.user_uid, message_id, locator.remote_instance_id),
            )
            if not rows:
                raise NotFoundError("message body parts were not recorded")
            parts = tuple(
                _BodyPartRecord(
                    body_kind=str(row["body_kind"]),
                    imap_part=str(row["imap_part"]),
                    content_type=str(row["content_type"]),
                    charset=str(row["charset"] or ""),
                    transfer_encoding=str(row["transfer_encoding"] or ""),
                    remote_size_bytes=int(row["remote_size_bytes"] or 0),
                )
                for row in rows
            )
            cid_rows = await fetch_all(
                connection,
                """
                SELECT id, content_id
                FROM message_attachments
                WHERE user_uid = %s AND message_id = %s
                  AND remote_instance_id = %s AND is_inline = 1
                """,
                (tenant.user_uid, message_id, locator.remote_instance_id),
            )
            cid_targets = {
                str(row["content_id"]).strip().strip("<>").casefold(): (
                    str(row["id"]),
                    f"/api/v2/mail/content/inline/{row['id']}",
                )
                for row in cid_rows
                if str(row["content_id"] or "").strip()
            }
            await uow.commit()
            return locator, parts, cid_targets

    async def _finish_body_fetch(
        self,
        tenant: TenantContext,
        message_id: str,
        stored: dict[str, StoredObject],
        search_text: str,
        referenced_ids: set[str],
        timestamp: float,
    ) -> None:
        async with SqlUnitOfWork(self.pool) as uow:
            connection = self._connection(uow)
            body = await self._body_row(connection, tenant, message_id, for_update=True)
            if str(body["state"]) != "fetching":
                raise ConflictError("message body is no longer fetching")
            locator = await self._message_locator(connection, tenant, message_id, for_update=True)
            repository = ObjectRepository(connection)
            unique_objects = {
                value.content_sha256: value for value in stored.values()
            }
            async with AsyncExitStack() as locks:
                for digest in sorted(unique_objects):
                    await locks.enter_async_context(repository.lock_object(digest))
                for body_kind, value in stored.items():
                    await repository.attach_reference(
                        value,
                        user_uid=tenant.user_uid,
                        reference_kind=f"message_body_{body_kind}",
                        reference_id=message_id,
                        last_accessed_at=timestamp,
                    )
                html_digest = stored.get("html").content_sha256 if stored.get("html") else None
                text_digest = stored.get("text").content_sha256 if stored.get("text") else None
                body_size = sum(value.original_size_bytes for value in unique_objects.values())
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        UPDATE message_bodies
                        SET html_object_sha256 = %s, text_object_sha256 = %s,
                            state = 'ready', body_size_bytes = %s,
                            checked_at = %s, cached_at = %s,
                            last_accessed_at = %s, last_error_class = '',
                            last_error_message = '', updated_at = %s
                        WHERE user_uid = %s AND message_id = %s AND state = 'fetching'
                        """,
                        (
                            html_digest,
                            text_digest,
                            body_size,
                            timestamp,
                            timestamp,
                            timestamp,
                            timestamp,
                            tenant.user_uid,
                            message_id,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise ConflictError("message body state changed concurrently")
                    await cursor.execute(
                        """
                        UPDATE messages
                        SET body_state = 'ready', search_state = 'ready', updated_at = %s
                        WHERE user_uid = %s AND id = %s
                        """,
                        (timestamp, tenant.user_uid, message_id),
                    )
                    await cursor.execute(
                        """
                        SELECT thread_id, subject, from_json, to_json, cc_json
                        FROM messages
                        WHERE user_uid = %s AND id = %s
                        """,
                        (tenant.user_uid, message_id),
                    )
                    message_row = await cursor.fetchone()
                    if not message_row:
                        raise NotFoundError("message was not found")
                    await cursor.execute(
                        """
                        SELECT message_id FROM body_search_documents
                        WHERE user_uid = %s AND message_id = %s
                        FOR UPDATE
                        """,
                        (tenant.user_uid, message_id),
                    )
                    exists = await cursor.fetchone()
                    participants = " ".join(
                        str(value or "") for value in message_row[2:5]
                    )
                    if exists:
                        await cursor.execute(
                            """
                            UPDATE body_search_documents
                            SET thread_id = %s, subject_text = %s,
                                participants_text = %s, body_text = %s,
                                index_version = index_version + 1, updated_at = %s
                            WHERE user_uid = %s AND message_id = %s
                            """,
                            (
                                message_row[0],
                                str(message_row[1] or ""),
                                participants,
                                search_text,
                                timestamp,
                                tenant.user_uid,
                                message_id,
                            ),
                        )
                    else:
                        await cursor.execute(
                            """
                            INSERT INTO body_search_documents (
                                message_id, user_uid, thread_id, subject_text,
                                participants_text, body_text, language,
                                index_version, updated_at
                            ) VALUES (%s, %s, %s, %s, %s, %s, '', 1, %s)
                            """,
                            (
                                message_id,
                                tenant.user_uid,
                                message_row[0],
                                str(message_row[1] or ""),
                                participants,
                                search_text,
                                timestamp,
                            ),
                        )
                    await cursor.execute(
                        """
                        UPDATE message_attachments
                        SET is_referenced_inline = CASE WHEN id IN ({}) THEN 1 ELSE 0 END,
                            cache_state = CASE WHEN id IN ({}) THEN 'queued' ELSE cache_state END,
                            updated_at = %s
                        WHERE user_uid = %s AND message_id = %s AND is_inline = 1
                        """.format(
                            ",".join("%s" for _ in referenced_ids) or "NULL",
                            ",".join("%s" for _ in referenced_ids) or "NULL",
                        ),
                        (
                            *sorted(referenced_ids),
                            *sorted(referenced_ids),
                            timestamp,
                            tenant.user_uid,
                            message_id,
                        ),
                    )
                if referenced_ids:
                    rows = await fetch_all(
                        connection,
                        """
                        SELECT id
                        FROM message_attachments
                        WHERE user_uid = %s AND message_id = %s
                          AND id IN ({})
                        ORDER BY id
                        """.format(",".join("%s" for _ in referenced_ids)),
                        (tenant.user_uid, message_id, *sorted(referenced_ids)),
                    )
                    for row in rows:
                        attachment_id = str(row["id"])
                        await self.publisher.enqueue(
                            connection,
                            tenant,
                            locator=locator,
                            job_kind="content.inline",
                            reference_id=attachment_id,
                            payload={"attachment_id": attachment_id},
                            now=timestamp,
                        )
                await uow.commit()

    async def _request_attachment_kind(
        self,
        tenant: TenantContext,
        attachment_id: str,
        *,
        job_kind: str,
        require_inline: bool,
        now: float | None,
    ) -> str:
        timestamp = float(time.time() if now is None else now)
        async with SqlUnitOfWork(self.pool) as uow:
            connection = self._connection(uow)
            attachment = await self._attachment_record(
                connection,
                tenant,
                attachment_id,
                for_update=True,
            )
            if attachment.is_inline is not require_inline:
                raise NotFoundError("attachment was not found")
            if attachment.cache_state in {"not_requested", "evicted", "failed"}:
                await self._transition_attachment(
                    connection,
                    tenant,
                    attachment_id,
                    "queued",
                    timestamp,
                )
            elif attachment.cache_state == "queued":
                pass
            elif attachment.cache_state == "unavailable":
                raise ConflictError("attachment is unavailable")
            else:
                raise ConflictError(f"attachment is already {attachment.cache_state}")
            job_id = await self.publisher.enqueue(
                connection,
                tenant,
                locator=attachment.locator,
                job_kind=job_kind,
                reference_id=attachment_id,
                payload={"attachment_id": attachment_id},
                now=timestamp,
            )
            await uow.commit()
            return job_id

    async def _fetch_attachment_bytes(
        self,
        tenant: TenantContext,
        attachment_id: str,
        *,
        object_kind: ObjectKind,
        reference_kind: str,
        require_inline: bool,
        supports_partial: bool,
        now: float | None,
    ) -> ContentFetchResult:
        timestamp = float(time.time() if now is None else now)
        async with SqlUnitOfWork(self.pool) as uow:
            connection = self._connection(uow)
            attachment = await self._attachment_record(
                connection,
                tenant,
                attachment_id,
                for_update=True,
            )
            if attachment.is_inline is not require_inline:
                raise NotFoundError("attachment was not found")
            if attachment.cache_state != "queued":
                raise ConflictError("attachment is not queued for fetching")
            await self._transition_attachment(
                connection,
                tenant,
                attachment_id,
                "fetching",
                timestamp,
            )
            await uow.commit()
        try:
            if attachment.remote_size_bytes > self.attachment_limit_bytes:
                raise PermanentError("attachment exceeds configured limit")
            source = self._attachment_stream(attachment, supports_partial)
            decoded = self._decoded_attachment_stream(
                source,
                attachment.transfer_encoding,
            )
            bounded = self._bounded_stream(decoded, self.attachment_limit_bytes)
            expected_size = (
                attachment.remote_size_bytes
                if attachment.transfer_encoding.casefold() in {"", "7bit", "8bit", "binary"}
                and attachment.remote_size_bytes > 0
                else None
            )
            stored = await self.store.put_stream(
                object_kind,
                bounded,
                expected_size=expected_size,
            )
            async with SqlUnitOfWork(self.pool) as uow:
                connection = self._connection(uow)
                current = await self._attachment_record(
                    connection,
                    tenant,
                    attachment_id,
                    for_update=True,
                )
                if current.cache_state != "fetching":
                    raise ConflictError("attachment is no longer fetching")
                repository = ObjectRepository(connection)
                async with repository.lock_object(stored.content_sha256):
                    await repository.attach_reference(
                        stored,
                        user_uid=tenant.user_uid,
                        reference_kind=reference_kind,
                        reference_id=attachment_id,
                        last_accessed_at=timestamp,
                    )
                    async with connection.cursor() as cursor:
                        await cursor.execute(
                            """
                            UPDATE message_attachments
                            SET content_sha256 = %s, cache_state = 'ready',
                                last_accessed_at = %s, updated_at = %s
                            WHERE user_uid = %s AND id = %s AND cache_state = 'fetching'
                            """,
                            (
                                stored.content_sha256,
                                timestamp,
                                timestamp,
                                tenant.user_uid,
                                attachment_id,
                            ),
                        )
                        if cursor.rowcount != 1:
                            raise ConflictError("attachment state changed concurrently")
                    await uow.commit()
            return ContentFetchResult(state="ready", content_sha256=stored.content_sha256)
        except BaseException as exc:
            await self._mark_attachment_failure(tenant, attachment_id, exc, timestamp)
            raise

    def _attachment_stream(
        self,
        attachment: _AttachmentRecord,
        supports_partial: bool,
    ) -> AsyncIterable[bytes]:
        part = validate_imap_part(attachment.imap_part)

        async def generate():
            if supports_partial and attachment.remote_size_bytes > 0:
                offset = 0
                while offset < attachment.remote_size_bytes:
                    count = min(
                        self.partial_chunk_bytes,
                        attachment.remote_size_bytes - offset,
                    )
                    received = 0
                    async for raw in self.transport.stream(
                        attachment.locator,
                        build_partial_fetch(part, offset, count),
                    ):
                        value = self._bytes_chunk(raw)
                        received += len(value)
                        if received > count:
                            raise PermanentError(
                                "partial attachment fetch returned an unexpected size"
                            )
                        if value:
                            yield value
                    if received != count:
                        raise PermanentError(
                            "partial attachment fetch returned an unexpected size"
                        )
                    offset += count
                return
            async for raw in self.transport.stream(
                attachment.locator,
                f"BODY.PEEK[{part}]",
            ):
                value = self._bytes_chunk(raw)
                if value:
                    yield value

        return generate()

    def _decoded_attachment_stream(
        self,
        chunks: AsyncIterable[bytes],
        encoding: str,
    ) -> AsyncIterable[bytes]:
        normalized = str(encoding or "").strip().casefold()

        async def passthrough():
            async for chunk in chunks:
                yield chunk

        async def base64_chunks():
            buffer = bytearray()
            try:
                async for chunk in chunks:
                    buffer.extend(re.sub(rb"\s+", b"", chunk))
                    complete = (len(buffer) // 4) * 4
                    if complete:
                        payload = bytes(buffer[:complete])
                        del buffer[:complete]
                        decoded = base64.b64decode(payload, validate=False)
                        if decoded:
                            yield decoded
                if buffer:
                    padding = (-len(buffer)) % 4
                    decoded = base64.b64decode(
                        bytes(buffer) + (b"=" * padding),
                        validate=False,
                    )
                    if decoded:
                        yield decoded
            except (binascii.Error, ValueError, TypeError) as exc:
                raise PermanentError("content transfer decoding failed") from exc

        async def quoted_printable_chunks():
            buffer = bytearray()
            async for chunk in chunks:
                buffer.extend(chunk)
            if buffer:
                yield quopri.decodestring(bytes(buffer))

        if normalized in {"", "7bit", "8bit", "binary"}:
            return passthrough()
        if normalized == "base64":
            return base64_chunks()
        if normalized in {"quoted-printable", "quopri"}:
            return quoted_printable_chunks()
        raise PermanentError("unsupported content transfer encoding")

    def _bounded_stream(
        self,
        chunks: AsyncIterable[bytes],
        limit: int,
    ) -> AsyncIterable[bytes]:
        async def generate():
            total = 0
            async for chunk in chunks:
                value = self._bytes_chunk(chunk)
                total += len(value)
                if total > int(limit):
                    raise PermanentError("attachment exceeds configured limit")
                if value:
                    yield value

        return generate()

    @staticmethod
    def _bytes_chunk(raw: bytes | bytearray | memoryview) -> bytes:
        if not isinstance(raw, (bytes, bytearray, memoryview)):
            raise TypeError("content stream chunks must be bytes-like")
        return bytes(raw)

    async def _message_locator(
        self,
        connection: aiomysql.Connection,
        tenant: TenantContext,
        message_id: str,
        *,
        for_update: bool = False,
    ) -> RemoteContentLocator:
        suffix = " FOR UPDATE" if for_update else ""
        row = await fetch_one(
            connection,
            """
            SELECT r.id AS remote_instance_id, r.account_id, a.provider_key,
                   mb.native_key, r.uidvalidity, r.remote_uid
            FROM message_remote_instances r
            JOIN mail_accounts a
              ON a.id = r.account_id AND a.user_uid = r.user_uid
            JOIN mailboxes mb
              ON mb.id = r.mailbox_id AND mb.user_uid = r.user_uid
            WHERE r.user_uid = %s AND r.message_id = %s
              AND r.remote_deleted = 0 AND a.status = 'active'
            ORDER BY r.last_seen_at DESC, r.id DESC
            LIMIT 1
            """ + suffix,
            (tenant.user_uid, str(message_id or "").strip()),
        )
        if row is None:
            raise NotFoundError("remote message instance was not found")
        return RemoteContentLocator(
            remote_instance_id=str(row["remote_instance_id"]),
            account_id=str(row["account_id"]),
            provider_key=str(row["provider_key"]),
            mailbox_native_key=str(row["native_key"]),
            uidvalidity=int(row["uidvalidity"]),
            remote_uid=int(row["remote_uid"]),
        )

    async def _require_locator(
        self,
        connection: aiomysql.Connection,
        tenant: TenantContext,
        *,
        message_id: str,
        remote_instance_id: str,
        for_update: bool,
    ) -> RemoteContentLocator:
        suffix = " FOR UPDATE" if for_update else ""
        row = await fetch_one(
            connection,
            """
            SELECT r.id AS remote_instance_id, r.account_id, a.provider_key,
                   mb.native_key, r.uidvalidity, r.remote_uid
            FROM message_remote_instances r
            JOIN mail_accounts a
              ON a.id = r.account_id AND a.user_uid = r.user_uid
            JOIN mailboxes mb
              ON mb.id = r.mailbox_id AND mb.user_uid = r.user_uid
            WHERE r.user_uid = %s AND r.message_id = %s AND r.id = %s
              AND r.remote_deleted = 0 AND a.status = 'active'
            """ + suffix,
            (tenant.user_uid, message_id, remote_instance_id),
        )
        if row is None:
            raise NotFoundError("remote message instance was not found")
        return RemoteContentLocator(
            remote_instance_id=str(row["remote_instance_id"]),
            account_id=str(row["account_id"]),
            provider_key=str(row["provider_key"]),
            mailbox_native_key=str(row["native_key"]),
            uidvalidity=int(row["uidvalidity"]),
            remote_uid=int(row["remote_uid"]),
        )

    async def _attachment_record(
        self,
        connection: aiomysql.Connection,
        tenant: TenantContext,
        attachment_id: str,
        *,
        for_update: bool = False,
    ) -> _AttachmentRecord:
        suffix = " FOR UPDATE" if for_update else ""
        row = await fetch_one(
            connection,
            """
            SELECT att.id, att.message_id, att.remote_instance_id, att.imap_part,
                   att.content_type, att.content_id, att.transfer_encoding,
                   att.remote_size_bytes, att.is_inline,
                   att.is_referenced_inline, att.cache_state,
                   r.account_id, a.provider_key, mb.native_key,
                   r.uidvalidity, r.remote_uid
            FROM message_attachments att
            JOIN message_remote_instances r
              ON r.id = att.remote_instance_id AND r.user_uid = att.user_uid
            JOIN mail_accounts a
              ON a.id = r.account_id AND a.user_uid = r.user_uid
            JOIN mailboxes mb
              ON mb.id = r.mailbox_id AND mb.user_uid = r.user_uid
            WHERE att.user_uid = %s AND att.id = %s
              AND r.remote_deleted = 0 AND a.status = 'active'
            """ + suffix,
            (tenant.user_uid, str(attachment_id or "").strip()),
        )
        if row is None:
            raise NotFoundError("attachment was not found")
        return _AttachmentRecord(
            id=str(row["id"]),
            message_id=str(row["message_id"]),
            remote_instance_id=str(row["remote_instance_id"]),
            imap_part=str(row["imap_part"]),
            content_type=str(row["content_type"]),
            content_id=str(row["content_id"] or ""),
            transfer_encoding=str(row["transfer_encoding"] or ""),
            remote_size_bytes=int(row["remote_size_bytes"] or 0),
            is_inline=bool(row["is_inline"]),
            is_referenced_inline=bool(row["is_referenced_inline"]),
            cache_state=str(row["cache_state"]),
            locator=RemoteContentLocator(
                remote_instance_id=str(row["remote_instance_id"]),
                account_id=str(row["account_id"]),
                provider_key=str(row["provider_key"]),
                mailbox_native_key=str(row["native_key"]),
                uidvalidity=int(row["uidvalidity"]),
                remote_uid=int(row["remote_uid"]),
            ),
        )

    async def _body_row(
        self,
        connection: aiomysql.Connection,
        tenant: TenantContext,
        message_id: str,
        *,
        for_update: bool,
    ) -> dict:
        suffix = " FOR UPDATE" if for_update else ""
        row = await fetch_one(
            connection,
            """
            SELECT message_id, state, html_object_sha256,
                   text_object_sha256, raw_eml_object_sha256,
                   body_size_bytes
            FROM message_bodies
            WHERE user_uid = %s AND message_id = %s
            """ + suffix,
            (tenant.user_uid, str(message_id or "").strip()),
        )
        if row is None:
            raise NotFoundError("message body was not found")
        return dict(row)

    async def _ensure_body_row(
        self,
        connection: aiomysql.Connection,
        tenant: TenantContext,
        message_id: str,
        timestamp: float,
    ) -> None:
        row = await fetch_one(
            connection,
            """
            SELECT message_id FROM message_bodies
            WHERE user_uid = %s AND message_id = %s
            FOR UPDATE
            """,
            (tenant.user_uid, message_id),
        )
        if row is not None:
            return
        async with connection.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO message_bodies (
                    message_id, user_uid, html_object_sha256,
                    text_object_sha256, raw_eml_object_sha256,
                    state, body_size_bytes, index_version, parser_version,
                    checked_at, cached_at, last_accessed_at,
                    last_error_class, last_error_message, updated_at
                ) VALUES (%s, %s, NULL, NULL, NULL, 'not_requested', 0,
                          0, 1, 0, NULL, 0, '', '', %s)
                """,
                (message_id, tenant.user_uid, timestamp),
            )

    async def _upsert_body_parts(
        self,
        connection: aiomysql.Connection,
        tenant: TenantContext,
        message_id: str,
        remote_instance_id: str,
        parts: tuple[tuple[str, MimePart], ...],
        timestamp: float,
    ) -> None:
        existing = await fetch_all(
            connection,
            """
            SELECT id, body_kind
            FROM message_body_parts
            WHERE user_uid = %s AND remote_instance_id = %s
            FOR UPDATE
            """,
            (tenant.user_uid, remote_instance_id),
        )
        existing_by_kind = {str(row["body_kind"]): str(row["id"]) for row in existing}
        desired_kinds = {kind for kind, _part in parts}
        async with connection.cursor() as cursor:
            for kind, part in parts:
                values = (
                    validate_imap_part(part.imap_part),
                    part.content_type,
                    part.charset or "",
                    part.transfer_encoding or "",
                    int(part.size),
                    timestamp,
                )
                if kind in existing_by_kind:
                    await cursor.execute(
                        """
                        UPDATE message_body_parts
                        SET message_id = %s, imap_part = %s, content_type = %s,
                            charset = %s, transfer_encoding = %s,
                            remote_size_bytes = %s, updated_at = %s
                        WHERE id = %s AND user_uid = %s
                        """,
                        (
                            message_id,
                            *values,
                            existing_by_kind[kind],
                            tenant.user_uid,
                        ),
                    )
                else:
                    await cursor.execute(
                        """
                        INSERT INTO message_body_parts (
                            id, user_uid, message_id, remote_instance_id,
                            body_kind, imap_part, content_type, charset,
                            transfer_encoding, remote_size_bytes,
                            created_at, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            new_id("bodypart"),
                            tenant.user_uid,
                            message_id,
                            remote_instance_id,
                            kind,
                            values[0],
                            values[1],
                            values[2],
                            values[3],
                            values[4],
                            timestamp,
                            timestamp,
                        ),
                    )
            obsolete = [identifier for kind, identifier in existing_by_kind.items() if kind not in desired_kinds]
            if obsolete:
                placeholders = ",".join("%s" for _ in obsolete)
                await cursor.execute(
                    f"DELETE FROM message_body_parts WHERE user_uid = %s AND id IN ({placeholders})",
                    (tenant.user_uid, *obsolete),
                )

    async def _upsert_attachments(
        self,
        connection: aiomysql.Connection,
        tenant: TenantContext,
        message_id: str,
        remote_instance_id: str,
        parts: tuple[MimePart, ...],
        inline_ids: set[str],
        timestamp: float,
    ) -> None:
        existing = await fetch_all(
            connection,
            """
            SELECT id, imap_part
            FROM message_attachments
            WHERE user_uid = %s AND remote_instance_id = %s
            FOR UPDATE
            """,
            (tenant.user_uid, remote_instance_id),
        )
        existing_by_part = {str(row["imap_part"]): str(row["id"]) for row in existing}
        desired_parts = {part.imap_part for part in parts}
        async with connection.cursor() as cursor:
            for part in parts:
                part_id = validate_imap_part(part.imap_part)
                is_inline = part_id in inline_ids
                disposition = "inline" if is_inline else (part.disposition or "attachment")
                if disposition not in {"attachment", "inline", "none"}:
                    disposition = "none"
                values = (
                    message_id,
                    part.content_type,
                    part.filename or "",
                    disposition,
                    part.content_id or "",
                    part.transfer_encoding or "",
                    int(part.size),
                    1 if is_inline else 0,
                    timestamp,
                )
                if part_id in existing_by_part:
                    await cursor.execute(
                        """
                        UPDATE message_attachments
                        SET message_id = %s, content_type = %s, filename = %s,
                            disposition = %s, content_id = %s,
                            transfer_encoding = %s, remote_size_bytes = %s,
                            is_inline = %s, updated_at = %s
                        WHERE id = %s AND user_uid = %s
                        """,
                        (*values, existing_by_part[part_id], tenant.user_uid),
                    )
                else:
                    await cursor.execute(
                        """
                        INSERT INTO message_attachments (
                            id, user_uid, message_id, remote_instance_id,
                            imap_part, filename, content_type, disposition,
                            content_id, transfer_encoding, remote_size_bytes,
                            content_sha256, is_inline, is_referenced_inline,
                            cache_state, last_accessed_at, created_at, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                                  %s, NULL, %s, 0, 'not_requested', 0, %s, %s)
                        """,
                        (
                            new_id("att"),
                            tenant.user_uid,
                            message_id,
                            remote_instance_id,
                            part_id,
                            part.filename or "",
                            part.content_type,
                            disposition,
                            part.content_id or "",
                            part.transfer_encoding or "",
                            int(part.size),
                            1 if is_inline else 0,
                            timestamp,
                            timestamp,
                        ),
                    )
            obsolete = [identifier for part_id, identifier in existing_by_part.items() if part_id not in desired_parts]
            if obsolete:
                placeholders = ",".join("%s" for _ in obsolete)
                await cursor.execute(
                    f"""
                    DELETE FROM message_attachments
                    WHERE user_uid = %s AND id IN ({placeholders})
                      AND content_sha256 IS NULL
                    """,
                    (tenant.user_uid, *obsolete),
                )

    async def _transition_attachment(
        self,
        connection: aiomysql.Connection,
        tenant: TenantContext,
        attachment_id: str,
        target_state: str,
        timestamp: float,
    ) -> bool:
        row = await fetch_one(
            connection,
            """
            SELECT cache_state FROM message_attachments
            WHERE user_uid = %s AND id = %s
            FOR UPDATE
            """,
            (tenant.user_uid, attachment_id),
        )
        if row is None:
            raise NotFoundError("attachment was not found")
        current = str(row["cache_state"])
        if current == target_state:
            return False
        if target_state not in _ALLOWED_ATTACHMENT_TRANSITIONS[current]:
            raise ConflictError(
                f"attachment state cannot transition from {current} to {target_state}"
            )
        async with connection.cursor() as cursor:
            await cursor.execute(
                """
                UPDATE message_attachments
                SET cache_state = %s, updated_at = %s
                WHERE user_uid = %s AND id = %s AND cache_state = %s
                """,
                (target_state, timestamp, tenant.user_uid, attachment_id, current),
            )
            if cursor.rowcount != 1:
                raise ConflictError("attachment state changed concurrently")
        return True

    async def _mark_body_failure(
        self,
        tenant: TenantContext,
        message_id: str,
        error: BaseException,
        timestamp: float,
    ) -> None:
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            return
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        UPDATE message_bodies
                        SET state = 'failed', last_error_class = %s,
                            last_error_message = 'content fetch failed', updated_at = %s
                        WHERE user_uid = %s AND message_id = %s AND state = 'fetching'
                        """,
                        (type(error).__name__[:96], timestamp, tenant.user_uid, message_id),
                    )
                    await cursor.execute(
                        """
                        UPDATE messages
                        SET body_state = 'failed', search_state = 'failed', updated_at = %s
                        WHERE user_uid = %s AND id = %s AND body_state = 'fetching'
                        """,
                        (timestamp, tenant.user_uid, message_id),
                    )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise

    async def _mark_attachment_failure(
        self,
        tenant: TenantContext,
        attachment_id: str,
        error: BaseException,
        timestamp: float,
    ) -> None:
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            return
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        UPDATE message_attachments
                        SET cache_state = 'failed', updated_at = %s
                        WHERE user_uid = %s AND id = %s AND cache_state = 'fetching'
                        """,
                        (timestamp, tenant.user_uid, attachment_id),
                    )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise

    async def _collect(self, chunks: AsyncIterable[bytes], limit: int) -> bytes:
        collected: list[bytes] = []
        total = 0
        async for raw in chunks:
            if not isinstance(raw, (bytes, bytearray, memoryview)):
                raise TypeError("content stream chunks must be bytes-like")
            value = bytes(raw)
            total += len(value)
            if total > int(limit):
                raise PermanentError("remote content exceeds configured limit")
            collected.append(value)
        return b"".join(collected)

    def _decode_transfer(self, value: bytes, encoding: str) -> bytes:
        normalized = str(encoding or "").strip().casefold()
        try:
            if normalized == "base64":
                return base64.b64decode(value, validate=False)
            if normalized in {"quoted-printable", "quopri"}:
                return quopri.decodestring(value)
            if normalized in {"", "7bit", "8bit", "binary"}:
                return value
        except (ValueError, TypeError) as exc:
            raise PermanentError("content transfer decoding failed") from exc
        raise PermanentError("unsupported content transfer encoding")

    def _decode_charset(self, value: bytes, charset: str) -> str:
        normalized = str(charset or "utf-8").strip() or "utf-8"
        try:
            return value.decode(normalized, errors="replace")
        except LookupError:
            return value.decode("utf-8", errors="replace")

    def _sanitize_html(
        self,
        value: str,
        cid_targets: dict[str, tuple[str, str]],
    ) -> tuple[str, set[str]]:
        parser = _Sanitizer(cid_targets)
        parser.feed(value)
        parser.close()
        return "".join(parser.output), set(parser.referenced_attachment_ids)

    def _html_text(self, value: str) -> str:
        parser = _TextExtractor()
        parser.feed(value)
        parser.close()
        return " ".join(parser.fragments)

    async def _store_text(self, kind: ObjectKind, value: str) -> StoredObject:
        return await self._store_bytes(kind, value.encode("utf-8"), compress=True)

    async def _store_bytes(
        self,
        kind: ObjectKind,
        value: bytes,
        *,
        compress: bool,
    ) -> StoredObject:
        original = bytes(value)
        stored_value = original
        compression = "none"
        if compress and original:
            candidate = gzip.compress(original, compresslevel=6, mtime=0)
            if len(candidate) < len(original):
                stored_value = candidate
                compression = "gzip"

        async def chunks():
            yield stored_value

        stored = await self.store.put_stream(
            kind,
            chunks(),
            expected_size=len(stored_value),
        )
        if compression == "none":
            return stored
        return replace(
            stored,
            original_size_bytes=len(original),
            stored_size_bytes=len(stored_value),
            compression=compression,
        )

    @staticmethod
    def _connection(uow: SqlUnitOfWork) -> aiomysql.Connection:
        if uow.connection is None:
            raise RuntimeError("unit of work connection is unavailable")
        return uow.connection
