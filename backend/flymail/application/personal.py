"""Tenant-scoped profile images, account icons, and safe signature helpers."""

from __future__ import annotations

import asyncio
import io
import time
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from typing import AsyncIterable

import aiomysql
from PIL import Image, ImageOps, UnidentifiedImageError

from flymail.api.schemas.personal import AccountIconResponse, ProfileResponse
from flymail.application.auth import AuthenticatedSession
from flymail.application.realtime import RealtimeService
from flymail.domain.enums import ObjectKind
from flymail.domain.errors import ApiContractError, NotFoundError
from flymail.infrastructure.db.pool import DatabasePool
from flymail.infrastructure.object_store.store import ObjectStore
from flymail.repositories.audit import AuditRepository
from flymail.repositories.base import TenantContext
from flymail.repositories.objects import ObjectRepository


MAX_PROFILE_IMAGE_BYTES = 5 * 1024 * 1024
MAX_PROFILE_IMAGE_PIXELS = 25_000_000
IMAGE_SIZE = 256
_ALLOWED_SIGNATURE_TAGS = {
    "a", "b", "blockquote", "br", "div", "em", "i", "li", "ol", "p",
    "span", "strong", "u", "ul",
}
_BLOCKED_SIGNATURE_TAGS = {"script", "style", "iframe", "object", "embed", "svg", "math"}


async def _chunks(value: bytes) -> AsyncIterable[bytes]:
    yield value


class _SignatureSanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.blocked_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        normalized = tag.casefold()
        if normalized in _BLOCKED_SIGNATURE_TAGS:
            self.blocked_depth += 1
            return
        if self.blocked_depth or normalized not in _ALLOWED_SIGNATURE_TAGS:
            return
        safe_attrs: list[str] = []
        if normalized == "a":
            for name, value in attrs:
                if name.casefold() != "href":
                    continue
                target = str(value or "").strip()
                if target.startswith(("https://", "http://", "mailto:")):
                    safe_attrs.append(f'href="{escape(target, quote=True)}"')
            safe_attrs.extend(('rel="noopener noreferrer"', 'target="_blank"'))
        suffix = (" " + " ".join(safe_attrs)) if safe_attrs else ""
        self.parts.append(f"<{normalized}{suffix}>")

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.casefold()
        if normalized in _BLOCKED_SIGNATURE_TAGS:
            if self.blocked_depth:
                self.blocked_depth -= 1
            return
        if not self.blocked_depth and normalized in _ALLOWED_SIGNATURE_TAGS and normalized != "br":
            self.parts.append(f"</{normalized}>")

    def handle_data(self, data: str) -> None:
        if not self.blocked_depth:
            self.parts.append(escape(data))

    def handle_entityref(self, name: str) -> None:
        if not self.blocked_depth:
            self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if not self.blocked_depth:
            self.parts.append(f"&#{name};")


def sanitize_signature_html(value: str) -> str:
    parser = _SignatureSanitizer()
    try:
        parser.feed(str(value or ""))
        parser.close()
    except Exception as exc:
        raise ApiContractError(
            "invalid_signature_html",
            "signature HTML is invalid",
            status_code=422,
        ) from exc
    return "".join(parser.parts)[:262144]


def _normalize_image_sync(data: bytes) -> bytes:
    if not data:
        raise ApiContractError("invalid_image", "image file is empty", status_code=422)
    if len(data) > MAX_PROFILE_IMAGE_BYTES:
        raise ApiContractError("image_too_large", "image file exceeds 5 MiB", status_code=422)
    output = io.BytesIO()
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.width < 1 or source.height < 1:
                raise ApiContractError("invalid_image", "image dimensions are invalid", status_code=422)
            if source.width * source.height > MAX_PROFILE_IMAGE_PIXELS:
                raise ApiContractError("image_too_large", "image dimensions are too large", status_code=422)
            source.load()
            normalized = ImageOps.exif_transpose(source).convert("RGB")
            normalized = ImageOps.fit(
                normalized,
                (IMAGE_SIZE, IMAGE_SIZE),
                method=Image.Resampling.LANCZOS,
            )
            normalized.save(output, format="WEBP", quality=88, method=6)
    except (UnidentifiedImageError, OSError) as exc:
        raise ApiContractError(
            "invalid_image",
            "image file is not a supported image",
            status_code=422,
        ) from exc
    return output.getvalue()


class PersonalService:
    def __init__(
        self,
        pool: DatabasePool,
        store: ObjectStore,
        realtime: RealtimeService,
        *,
        now_fn=time.time,
    ) -> None:
        if not isinstance(pool, DatabasePool):
            raise TypeError("pool must be DatabasePool")
        if not isinstance(store, ObjectStore):
            raise TypeError("store must be ObjectStore")
        if not isinstance(realtime, RealtimeService):
            raise TypeError("realtime must be RealtimeService")
        self.pool = pool
        self.store = store
        self.realtime = realtime
        self.now_fn = now_fn

    async def profile(self, session: AuthenticatedSession) -> ProfileResponse:
        tenant = TenantContext(session.user.id)
        async with self.pool.acquire() as connection:
            async with connection.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(
                    """
                    SELECT u.id, u.username, u.role, p.nickname,
                           p.avatar_object_sha256
                    FROM users u
                    JOIN user_profiles p ON p.user_uid = u.id
                    WHERE u.id = %s
                    """,
                    (tenant.user_uid,),
                )
                row = await cursor.fetchone()
        if row is None:
            raise NotFoundError("profile was not found")
        return ProfileResponse(
            user_uid=str(row["id"]),
            username=str(row["username"]),
            role=str(row["role"]),
            nickname=str(row["nickname"] or ""),
            avatar_url=(
                "/api/v2/profile/avatar" if row["avatar_object_sha256"] else None
            ),
        )

    async def update_profile(
        self,
        session: AuthenticatedSession,
        nickname: str,
        *,
        request_id: str,
    ) -> ProfileResponse:
        tenant = TenantContext(session.user.id)
        timestamp = float(self.now_fn())
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        UPDATE user_profiles SET nickname = %s, updated_at = %s
                        WHERE user_uid = %s
                        """,
                        (str(nickname or "").strip(), timestamp, tenant.user_uid),
                    )
                    if cursor.rowcount != 1:
                        raise NotFoundError("profile was not found")
                await AuditRepository(connection).append(
                    event_type="profile.update",
                    result_code="success",
                    request_id=request_id,
                    user_uid=tenant.user_uid,
                    actor_user_uid=tenant.user_uid,
                    resource_type="user_profile",
                    resource_id=tenant.user_uid,
                    safe_metadata={"fields": ["nickname"]},
                    now=timestamp,
                )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
        await self._publish_profile(tenant)
        return await self.profile(session)

    async def upload_avatar(
        self,
        session: AuthenticatedSession,
        data: bytes,
        *,
        request_id: str,
    ) -> ProfileResponse:
        tenant = TenantContext(session.user.id)
        normalized = await asyncio.to_thread(_normalize_image_sync, bytes(data))
        stored = await self.store.put_stream(ObjectKind.USER_AVATAR, _chunks(normalized), len(normalized))
        old_digest: str | None = None
        timestamp = float(self.now_fn())
        try:
            async with self.pool.acquire() as connection:
                await connection.begin()
                try:
                    async with connection.cursor() as cursor:
                        await cursor.execute(
                            "SELECT avatar_object_sha256 FROM user_profiles WHERE user_uid=%s FOR UPDATE",
                            (tenant.user_uid,),
                        )
                        row = await cursor.fetchone()
                        if row is None:
                            raise NotFoundError("profile was not found")
                        old_digest = str(row[0]) if row[0] else None
                    repository = ObjectRepository(connection)
                    if old_digest:
                        await repository.detach_reference(
                            user_uid=tenant.user_uid,
                            reference_kind="user_avatar",
                            reference_id=tenant.user_uid,
                        )
                    await repository.attach_reference(
                        stored,
                        user_uid=tenant.user_uid,
                        reference_kind="user_avatar",
                        reference_id=tenant.user_uid,
                        pinned=True,
                        last_accessed_at=timestamp,
                    )
                    async with connection.cursor() as cursor:
                        await cursor.execute(
                            "UPDATE user_profiles SET avatar_object_sha256=%s, updated_at=%s WHERE user_uid=%s",
                            (stored.content_sha256, timestamp, tenant.user_uid),
                        )
                    await AuditRepository(connection).append(
                        event_type="profile.avatar_updated",
                        result_code="success",
                        request_id=request_id,
                        user_uid=tenant.user_uid,
                        actor_user_uid=tenant.user_uid,
                        resource_type="user_profile",
                        resource_id=tenant.user_uid,
                        safe_metadata={"image_format": "webp", "size": IMAGE_SIZE},
                        now=timestamp,
                    )
                    await connection.commit()
                except Exception:
                    await connection.rollback()
                    raise
        except Exception:
            await self._remove_unreferenced(stored.content_sha256)
            raise
        if old_digest and old_digest != stored.content_sha256:
            await self._remove_unreferenced(old_digest)
        await self._publish_profile(tenant)
        return await self.profile(session)

    async def avatar_bytes(self, session: AuthenticatedSession) -> bytes:
        digest = await self._profile_digest(session.user.id)
        return await self._read_object(digest)

    async def set_account_icon(
        self,
        session: AuthenticatedSession,
        account_id: str,
        *,
        mode: str,
        value: str,
        request_id: str,
    ) -> AccountIconResponse:
        tenant = TenantContext(session.user.id)
        normalized_account = str(account_id or "").strip()
        timestamp = float(self.now_fn())
        old_digest: str | None = None
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        SELECT icon_object_sha256 FROM mail_accounts
                        WHERE id=%s AND user_uid=%s FOR UPDATE
                        """,
                        (normalized_account, tenant.user_uid),
                    )
                    row = await cursor.fetchone()
                    if row is None:
                        raise NotFoundError("mail account was not found")
                    old_digest = str(row[0]) if row[0] else None
                if old_digest:
                    await ObjectRepository(connection).detach_reference(
                        user_uid=tenant.user_uid,
                        reference_kind="account_icon",
                        reference_id=normalized_account,
                    )
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        UPDATE mail_accounts
                        SET icon_mode=%s, icon_value=%s,
                            icon_object_sha256=NULL, updated_at=%s
                        WHERE id=%s AND user_uid=%s
                        """,
                        (mode, value, timestamp, normalized_account, tenant.user_uid),
                    )
                await self._audit_icon(connection, tenant, normalized_account, request_id, mode, timestamp)
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
        if old_digest:
            await self._remove_unreferenced(old_digest)
        return await self.account_icon(session, normalized_account)

    async def upload_account_icon(
        self,
        session: AuthenticatedSession,
        account_id: str,
        data: bytes,
        *,
        request_id: str,
    ) -> AccountIconResponse:
        tenant = TenantContext(session.user.id)
        normalized_account = str(account_id or "").strip()
        normalized = await asyncio.to_thread(_normalize_image_sync, bytes(data))
        stored = await self.store.put_stream(ObjectKind.ACCOUNT_ICON, _chunks(normalized), len(normalized))
        timestamp = float(self.now_fn())
        old_digest: str | None = None
        try:
            async with self.pool.acquire() as connection:
                await connection.begin()
                try:
                    async with connection.cursor() as cursor:
                        await cursor.execute(
                            """
                            SELECT icon_object_sha256 FROM mail_accounts
                            WHERE id=%s AND user_uid=%s FOR UPDATE
                            """,
                            (normalized_account, tenant.user_uid),
                        )
                        row = await cursor.fetchone()
                        if row is None:
                            raise NotFoundError("mail account was not found")
                        old_digest = str(row[0]) if row[0] else None
                    repository = ObjectRepository(connection)
                    if old_digest:
                        await repository.detach_reference(
                            user_uid=tenant.user_uid,
                            reference_kind="account_icon",
                            reference_id=normalized_account,
                        )
                    await repository.attach_reference(
                        stored,
                        user_uid=tenant.user_uid,
                        reference_kind="account_icon",
                        reference_id=normalized_account,
                        pinned=True,
                        last_accessed_at=timestamp,
                    )
                    async with connection.cursor() as cursor:
                        await cursor.execute(
                            """
                            UPDATE mail_accounts
                            SET icon_mode='uploaded', icon_value='',
                                icon_object_sha256=%s, updated_at=%s
                            WHERE id=%s AND user_uid=%s
                            """,
                            (stored.content_sha256, timestamp, normalized_account, tenant.user_uid),
                        )
                    await self._audit_icon(
                        connection, tenant, normalized_account, request_id, "uploaded", timestamp
                    )
                    await connection.commit()
                except Exception:
                    await connection.rollback()
                    raise
        except Exception:
            await self._remove_unreferenced(stored.content_sha256)
            raise
        if old_digest and old_digest != stored.content_sha256:
            await self._remove_unreferenced(old_digest)
        return await self.account_icon(session, normalized_account)

    async def account_icon(
        self,
        session: AuthenticatedSession,
        account_id: str,
    ) -> AccountIconResponse:
        tenant = TenantContext(session.user.id)
        normalized_account = str(account_id or "").strip()
        async with self.pool.acquire() as connection:
            async with connection.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(
                    """
                    SELECT id, icon_mode, icon_value, icon_object_sha256
                    FROM mail_accounts WHERE id=%s AND user_uid=%s
                    """,
                    (normalized_account, tenant.user_uid),
                )
                row = await cursor.fetchone()
        if row is None:
            raise NotFoundError("mail account was not found")
        mode = str(row["icon_mode"] or "provider")
        if row["icon_object_sha256"]:
            mode = "uploaded"
        return AccountIconResponse(
            account_id=normalized_account,
            mode=mode,
            value=str(row["icon_value"] or ""),
            content_url=(
                f"/api/v2/accounts/{normalized_account}/icon/content"
                if row["icon_object_sha256"] else None
            ),
        )

    async def account_icon_bytes(
        self,
        session: AuthenticatedSession,
        account_id: str,
    ) -> bytes:
        tenant = TenantContext(session.user.id)
        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "SELECT icon_object_sha256 FROM mail_accounts WHERE id=%s AND user_uid=%s",
                    (str(account_id or "").strip(), tenant.user_uid),
                )
                row = await cursor.fetchone()
        if row is None or not row[0]:
            raise NotFoundError("uploaded account icon was not found")
        return await self._read_object(str(row[0]))

    async def _profile_digest(self, user_uid: str) -> str:
        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "SELECT avatar_object_sha256 FROM user_profiles WHERE user_uid=%s",
                    (user_uid,),
                )
                row = await cursor.fetchone()
        if row is None or not row[0]:
            raise NotFoundError("profile avatar was not found")
        return str(row[0])

    async def _read_object(self, digest: str) -> bytes:
        async with self.store.open(digest) as handle:
            return await asyncio.to_thread(handle.read)

    async def _remove_unreferenced(self, digest: str) -> None:
        async with self.pool.acquire() as connection:
            await self.store.remove_unreferenced(digest, ObjectRepository(connection))

    async def _publish_profile(self, tenant: TenantContext) -> None:
        await self.realtime.publish(
            tenant,
            event_type="settings.updated",
            aggregate_type="user_profile",
            aggregate_id=tenant.user_uid,
            payload={"settings_scope": "profile"},
        )

    @staticmethod
    async def _audit_icon(
        connection,
        tenant: TenantContext,
        account_id: str,
        request_id: str,
        mode: str,
        timestamp: float,
    ) -> None:
        await AuditRepository(connection).append(
            event_type="account.icon_updated",
            result_code="success",
            request_id=request_id,
            user_uid=tenant.user_uid,
            actor_user_uid=tenant.user_uid,
            resource_type="mail_account",
            resource_id=account_id,
            safe_metadata={"mode": mode},
            now=timestamp,
        )
