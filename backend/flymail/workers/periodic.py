"""Durable periodic polling scheduler for active FlyMail accounts."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

import aiomysql

from flymail.infrastructure.db.pool import DatabasePool
from flymail.repositories.jobs import JobRepository, JobSpec


async def schedule_due_sync_jobs(
    pool: DatabasePool,
    *,
    now: float | None = None,
    limit: int = 100,
) -> int:
    if not isinstance(pool, DatabasePool):
        raise TypeError("pool must be DatabasePool")
    timestamp = float(time.time() if now is None else now)
    normalized_limit = int(limit)
    if normalized_limit < 1 or normalized_limit > 1000:
        raise ValueError("limit must be between 1 and 1000")
    async with pool.acquire() as connection:
        await connection.begin()
        try:
            async with connection.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(
                    """
                    SELECT a.id AS account_id, a.user_uid, a.provider_key,
                           a.poll_interval_seconds
                    FROM account_runtime_state runtime
                    JOIN mail_accounts a
                      ON a.id=runtime.account_id AND a.user_uid=runtime.user_uid
                    JOIN users u ON u.id=a.user_uid
                    WHERE a.status='active' AND u.enabled=1
                      AND runtime.status IN ('active','normal','quiet','degraded')
                      AND runtime.next_reconcile_at <= %s
                      AND runtime.backoff_until <= %s
                    ORDER BY runtime.next_reconcile_at, a.id
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                    """,
                    (timestamp, timestamp, normalized_limit),
                )
                rows = list(await cursor.fetchall())
            repository = JobRepository(connection)
            for row in rows:
                account_id = str(row["account_id"])
                await repository.enqueue(
                    JobSpec(
                        queue_name="reconcile",
                        job_kind="sync.reconcile",
                        payload={"account_id": account_id},
                        user_uid=str(row["user_uid"]),
                        account_id=account_id,
                        provider_key=str(row["provider_key"]),
                        priority=100,
                        available_at=timestamp,
                        max_attempts=10,
                        dedupe_key=f"periodic:sync.reconcile:{account_id}",
                    ),
                    now=timestamp,
                )
                next_at = timestamp + max(int(row["poll_interval_seconds"] or 300), 5)
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        UPDATE account_runtime_state
                        SET next_reconcile_at=%s, updated_at=%s
                        WHERE account_id=%s AND user_uid=%s
                        """,
                        (
                            next_at,
                            timestamp,
                            account_id,
                            str(row["user_uid"]),
                        ),
                    )
            await connection.commit()
            return len(rows)
        except Exception:
            await connection.rollback()
            raise


async def periodic_sync_loop(
    pool: DatabasePool,
    stop_event: asyncio.Event,
    *,
    interval_seconds: float = 5.0,
    now_fn: Callable[[], float] = time.time,
) -> None:
    interval = float(interval_seconds)
    if interval <= 0:
        raise ValueError("interval_seconds must be positive")
    while not stop_event.is_set():
        await schedule_due_sync_jobs(pool, now=float(now_fn()))
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except TimeoutError:
            continue


__all__ = ["periodic_sync_loop", "schedule_due_sync_jobs"]
