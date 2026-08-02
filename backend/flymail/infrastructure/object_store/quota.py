"""Per-user logical cache usage and body-cache eviction."""

from __future__ import annotations

from flymail.domain.enums import ObjectKind
from flymail.infrastructure.db.pool import DatabasePool
from flymail.infrastructure.object_store.models import EvictionResult
from flymail.infrastructure.object_store.store import ObjectStore
from flymail.repositories.objects import (
    BODY_CACHE_REFERENCE_KINDS,
    ObjectLockUnavailable,
    ObjectRepository,
)


class QuotaService:
    def __init__(self, pool: DatabasePool, store: ObjectStore) -> None:
        self.pool = pool
        self.store = store

    async def get_user_usage(self, user_uid: str, kinds: set[ObjectKind]) -> int:
        async with self.pool.acquire() as connection:
            repository = ObjectRepository(connection)
            return await repository.get_user_usage(user_uid, kinds)

    async def _get_body_usage(self, user_uid: str) -> int:
        async with self.pool.acquire() as connection:
            repository = ObjectRepository(connection)
            return await repository.get_user_usage_for_reference_kinds(
                user_uid,
                BODY_CACHE_REFERENCE_KINDS,
            )

    async def _get_attachment_usage(self, user_uid: str) -> int:
        async with self.pool.acquire() as connection:
            repository = ObjectRepository(connection)
            return await repository.get_user_usage_for_reference_kinds(
                user_uid,
                ("message_attachment",),
            )

    async def evict_body_cache(self, user_uid: str, limit_bytes: int) -> EvictionResult:
        normalized_limit = int(limit_bytes)
        if normalized_limit < 0:
            raise ValueError("quota limit must be non-negative")

        before = await self._get_body_usage(user_uid)
        if normalized_limit == 0 or before <= normalized_limit:
            return EvictionResult(before_bytes=before, after_bytes=before)

        async with self.pool.acquire() as connection:
            candidates = await ObjectRepository(connection).list_body_eviction_candidates(user_uid)

        logical_released = 0
        physical_released = 0
        object_count = 0
        message_ids: set[str] = set()
        current = before

        for candidate in candidates:
            if current <= normalized_limit:
                break
            detached = await self._detach_candidate(user_uid, candidate.content_sha256)
            if detached is None:
                continue

            logical_released += detached.logical_bytes
            current = max(0, current - detached.logical_bytes)
            object_count += 1
            message_ids.update(detached.message_ids)
            if await self._remove_unreferenced(detached.content_sha256):
                physical_released += detached.logical_bytes

        after = await self._get_body_usage(user_uid)
        return EvictionResult(
            before_bytes=before,
            after_bytes=after,
            logical_bytes_released=logical_released,
            physical_bytes_released=physical_released,
            message_count=len(message_ids),
            object_count=object_count,
        )

    async def evict_attachment_cache(
        self,
        user_uid: str,
        limit_bytes: int,
    ) -> EvictionResult:
        normalized_limit = int(limit_bytes)
        if normalized_limit < 0:
            raise ValueError("quota limit must be non-negative")
        before = await self._get_attachment_usage(user_uid)
        if normalized_limit == 0 or before <= normalized_limit:
            return EvictionResult(before_bytes=before, after_bytes=before)
        async with self.pool.acquire() as connection:
            candidates = await ObjectRepository(connection).list_attachment_eviction_candidates(
                user_uid
            )
        logical_released = 0
        physical_released = 0
        object_count = 0
        attachment_ids: set[str] = set()
        current = before
        for candidate in candidates:
            if current <= normalized_limit:
                break
            detached = await self._detach_attachment_candidate(
                user_uid,
                candidate.content_sha256,
            )
            if detached is None:
                continue
            logical_released += detached.logical_bytes
            current = max(0, current - detached.logical_bytes)
            object_count += 1
            attachment_ids.update(detached.attachment_ids)
            if await self._remove_unreferenced(detached.content_sha256):
                physical_released += detached.logical_bytes
        after = await self._get_attachment_usage(user_uid)
        return EvictionResult(
            before_bytes=before,
            after_bytes=after,
            logical_bytes_released=logical_released,
            physical_bytes_released=physical_released,
            message_count=len(attachment_ids),
            object_count=object_count,
        )

    async def _detach_candidate(self, user_uid: str, content_sha256: str):
        async with self.pool.acquire() as connection:
            repository = ObjectRepository(connection)
            try:
                async with repository.lock_object(content_sha256, timeout_seconds=0):
                    await connection.begin()
                    try:
                        detached = await repository.detach_body_digest_for_user(
                            user_uid,
                            content_sha256,
                        )
                        if detached is None:
                            await connection.rollback()
                            return None
                        await connection.commit()
                        return detached
                    except Exception:
                        await connection.rollback()
                        raise
            except ObjectLockUnavailable:
                return None

    async def _detach_attachment_candidate(
        self,
        user_uid: str,
        content_sha256: str,
    ):
        async with self.pool.acquire() as connection:
            repository = ObjectRepository(connection)
            try:
                async with repository.lock_object(content_sha256, timeout_seconds=0):
                    await connection.begin()
                    try:
                        detached = await repository.detach_attachment_digest_for_user(
                            user_uid,
                            content_sha256,
                        )
                        if detached is None:
                            await connection.rollback()
                            return None
                        await connection.commit()
                        return detached
                    except Exception:
                        await connection.rollback()
                        raise
            except ObjectLockUnavailable:
                return None

    async def _remove_unreferenced(self, content_sha256: str) -> bool:
        async with self.pool.acquire() as connection:
            repository = ObjectRepository(connection)
            return await self.store.remove_unreferenced(content_sha256, repository)
