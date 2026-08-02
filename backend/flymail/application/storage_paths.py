"""Administrator-authorized storage roots and tenant-safe relative browsing."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path, PurePosixPath

import aiomysql

from flymail.api.schemas.personal import (
    StorageBrowseResponse,
    StorageEntry,
    StorageRootResponse,
)
from flymail.application.auth import AuthenticatedSession
from flymail.domain.errors import ApiContractError, NotFoundError
from flymail.domain.ids import new_id
from flymail.infrastructure.db.pool import DatabasePool
from flymail.repositories.audit import AuditRepository
from flymail.repositories.users import User


MAX_BROWSE_ITEMS = 500


def _safe_relative_path(value: str) -> PurePosixPath:
    normalized = str(value or "").strip()
    if not normalized:
        return PurePosixPath(".")
    if "\\" in normalized or "\x00" in normalized:
        raise ApiContractError("unsafe_storage_path", "storage path is invalid", status_code=422)
    relative = PurePosixPath(normalized)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise ApiContractError("unsafe_storage_path", "storage path is invalid", status_code=422)
    return relative


class StoragePathService:
    def __init__(
        self,
        pool: DatabasePool,
        data_root: Path,
        *,
        now_fn=time.time,
    ) -> None:
        if not isinstance(pool, DatabasePool):
            raise TypeError("pool must be DatabasePool")
        self.pool = pool
        self.data_root = Path(data_root).resolve()
        self.now_fn = now_fn

    async def create_root(
        self,
        admin: User,
        *,
        label: str,
        path: str,
        visibility_scope: str,
        user_uid: str | None,
        request_id: str,
    ) -> StorageRootResponse:
        resolved = await asyncio.to_thread(Path(path).expanduser().resolve)
        if not resolved.is_relative_to(self.data_root):
            raise ApiContractError(
                "storage_root_outside_data",
                "storage root must be under the FlyMail data directory",
                status_code=422,
            )
        if resolved.is_symlink() or not resolved.is_dir():
            raise ApiContractError(
                "invalid_storage_root",
                "storage root must be a readable directory",
                status_code=422,
            )
        if not os.access(resolved, os.R_OK):
            raise ApiContractError(
                "storage_root_unreadable",
                "storage root is not readable",
                status_code=422,
            )
        selected_user = str(user_uid or "").strip() or None
        if visibility_scope == "user" and not selected_user:
            raise ApiContractError(
                "storage_root_user_required",
                "user-scoped storage root requires a user",
                status_code=422,
            )
        if visibility_scope == "all":
            selected_user = None
        root_id = new_id("storage")
        timestamp = float(self.now_fn())
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                if selected_user:
                    async with connection.cursor() as cursor:
                        await cursor.execute("SELECT id FROM users WHERE id=%s", (selected_user,))
                        if await cursor.fetchone() is None:
                            raise NotFoundError("storage-root user was not found")
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        INSERT INTO authorized_storage_roots (
                            id, user_uid, label, root_path, visibility_scope,
                            enabled, created_by, created_at, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, 1, %s, %s, %s)
                        """,
                        (
                            root_id, selected_user, str(label).strip(), str(resolved),
                            visibility_scope, admin.id, timestamp, timestamp,
                        ),
                    )
                await AuditRepository(connection).append(
                    event_type="storage.root_created",
                    result_code="success",
                    request_id=request_id,
                    actor_user_uid=admin.id,
                    user_uid=selected_user,
                    resource_type="authorized_storage_root",
                    resource_id=root_id,
                    safe_metadata={"visibility_scope": visibility_scope},
                    now=timestamp,
                )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
        return StorageRootResponse(
            id=root_id,
            label=str(label).strip(),
            visibility_scope=visibility_scope,
            user_uid=selected_user,
        )

    async def list_roots(
        self,
        session: AuthenticatedSession,
    ) -> tuple[StorageRootResponse, ...]:
        async with self.pool.acquire() as connection:
            async with connection.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(
                    """
                    SELECT id, label, visibility_scope, user_uid
                    FROM authorized_storage_roots
                    WHERE enabled=1 AND (
                        visibility_scope='all' OR user_uid=%s
                    )
                    ORDER BY label, id
                    LIMIT 200
                    """,
                    (session.user.id,),
                )
                rows = [dict(row) for row in await cursor.fetchall()]
        return tuple(
            StorageRootResponse(
                id=str(row["id"]),
                label=str(row["label"]),
                visibility_scope=str(row["visibility_scope"]),
                user_uid=str(row["user_uid"]) if row["user_uid"] else None,
            )
            for row in rows
        )

    async def browse(
        self,
        session: AuthenticatedSession,
        root_id: str,
        relative_path: str,
    ) -> StorageBrowseResponse:
        normalized_id = str(root_id or "").strip()
        async with self.pool.acquire() as connection:
            async with connection.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(
                    """
                    SELECT id, root_path
                    FROM authorized_storage_roots
                    WHERE id=%s AND enabled=1 AND (
                        visibility_scope='all' OR user_uid=%s
                    )
                    """,
                    (normalized_id, session.user.id),
                )
                row = await cursor.fetchone()
        if row is None:
            raise NotFoundError("storage root was not found")
        relative = _safe_relative_path(relative_path)
        root = await asyncio.to_thread(Path(str(row["root_path"])).resolve)
        target = root if relative == PurePosixPath(".") else root.joinpath(*relative.parts)
        if target.is_symlink():
            raise ApiContractError("unsafe_storage_path", "storage path is unsafe", status_code=422)
        resolved = await asyncio.to_thread(target.resolve)
        if not resolved.is_relative_to(root) or not resolved.is_dir():
            raise ApiContractError("unsafe_storage_path", "storage path is unsafe", status_code=422)
        entries = await asyncio.to_thread(self._list_entries, root, resolved)
        rendered_path = "" if relative == PurePosixPath(".") else relative.as_posix()
        return StorageBrowseResponse(
            root_id=normalized_id,
            path=rendered_path,
            items=entries,
        )

    @staticmethod
    def _list_entries(root: Path, directory: Path) -> tuple[StorageEntry, ...]:
        entries: list[StorageEntry] = []
        for candidate in sorted(directory.iterdir(), key=lambda item: item.name.casefold()):
            if len(entries) >= MAX_BROWSE_ITEMS:
                break
            if candidate.name.startswith(".") or candidate.is_symlink():
                continue
            try:
                resolved = candidate.resolve()
                if not resolved.is_relative_to(root):
                    continue
                if candidate.is_dir():
                    entry_type = "directory"
                    size = 0
                elif candidate.is_file():
                    entry_type = "file"
                    size = max(int(candidate.stat().st_size), 0)
                else:
                    continue
            except OSError:
                continue
            entries.append(
                StorageEntry(
                    name=candidate.name,
                    relative_path=resolved.relative_to(root).as_posix(),
                    entry_type=entry_type,
                    size_bytes=size,
                )
            )
        return tuple(entries)
