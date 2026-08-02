"""Worker handler for quota-driven body and ordinary-attachment cache cleanup."""

from __future__ import annotations

from collections.abc import Mapping

from flymail.infrastructure.db.pool import DatabasePool
from flymail.infrastructure.object_store.quota import QuotaService
from flymail.infrastructure.object_store.store import ObjectStore
from flymail.workers.dispatcher import JobContext, JobOutcome


class CacheCleanupHandler:
    def __init__(self, pool: DatabasePool, store: ObjectStore) -> None:
        if not isinstance(pool, DatabasePool):
            raise TypeError("pool must be DatabasePool")
        if not isinstance(store, ObjectStore):
            raise TypeError("store must be ObjectStore")
        self.quota = QuotaService(pool, store)

    async def __call__(
        self,
        context: JobContext,
        payload: Mapping[str, object],
    ) -> JobOutcome:
        user_uid = str(payload.get("user_uid") or "").strip()
        if not user_uid or user_uid != str(context.user_uid or "").strip():
            return JobOutcome.fail(
                "InvalidCleanupScope",
                "cache cleanup user scope does not match the job",
            )
        try:
            body_limit = int(payload.get("body_cache_quota_bytes") or 0)
            attachment_limit = int(payload.get("attachment_cache_quota_bytes") or 0)
        except (TypeError, ValueError):
            return JobOutcome.fail("InvalidCleanupQuota", "cache cleanup quota is invalid")
        if body_limit < 0 or attachment_limit < 0:
            return JobOutcome.fail("InvalidCleanupQuota", "cache cleanup quota is invalid")
        if context.stop_event.is_set():
            return JobOutcome.retry(
                "WorkerStopping",
                "cache cleanup paused for shutdown",
                base_seconds=1,
                max_seconds=30,
            )
        await self.quota.evict_body_cache(user_uid, body_limit)
        if context.stop_event.is_set():
            return JobOutcome.retry(
                "WorkerStopping",
                "cache cleanup paused for shutdown",
                base_seconds=1,
                max_seconds=30,
            )
        await self.quota.evict_attachment_cache(user_uid, attachment_limit)
        return JobOutcome.success()
