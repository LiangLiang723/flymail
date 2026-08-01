"""Worker process heartbeat over active durable job leases."""

from __future__ import annotations

import time
from collections.abc import Callable

from flymail.infrastructure.db.pool import DatabasePool
from flymail.repositories.jobs import JobRepository
from flymail.repositories.runtime import RuntimeRepository


class WorkerHeartbeatService:
    """Extend all active leases currently owned by one Worker process."""

    def __init__(
        self,
        pool: DatabasePool,
        *,
        now_fn: Callable[[], float] = time.time,
        lease_seconds: int = 60,
    ) -> None:
        if int(lease_seconds) < 1:
            raise ValueError("lease_seconds must be at least 1")
        self.pool = pool
        self.now_fn = now_fn
        self.lease_seconds = int(lease_seconds)

    async def touch(self, worker_id: str, role: str) -> None:
        normalized_role = str(role or "").strip()
        if normalized_role != "worker":
            raise ValueError("unsupported worker role")
        timestamp = float(self.now_fn())
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                await RuntimeRepository(connection).touch_process(
                    worker_id,
                    normalized_role,
                    now=timestamp,
                )
                await JobRepository(connection).touch_worker_jobs(
                    worker_id,
                    lease_seconds=self.lease_seconds,
                    now=timestamp,
                )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
