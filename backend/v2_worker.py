"""Isolated FlyMail V2 Worker process lifecycle.

The foundation Worker maintains migrations, lease recovery, and heartbeats. It
intentionally does not claim protocol jobs until handlers are registered by a
later implementation plan.
"""

from __future__ import annotations

import asyncio
import signal
import sys
import time
from collections.abc import Callable

from flymail.config import FlyMailSettings
from flymail.domain.errors import ConfigurationError
from flymail.domain.ids import new_id
from flymail.infrastructure.db.migrations.runner import run_migrations
from flymail.infrastructure.db.pool import DatabasePool
from flymail.repositories.jobs import JobRepository
from flymail.workers.lease import WorkerHeartbeatService


async def run_worker(
    *,
    stop_event: asyncio.Event | None = None,
    now_fn: Callable[[], float] = time.time,
) -> None:
    settings = FlyMailSettings.from_env("worker")
    pool = await DatabasePool.create(settings)
    worker_id = new_id("wrk")
    stop = stop_event or asyncio.Event()
    installed_signals: list[signal.Signals] = []
    loop = asyncio.get_running_loop()

    if stop_event is None:
        for signum in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(signum, stop.set)
                installed_signals.append(signum)
            except (NotImplementedError, RuntimeError):
                continue

    try:
        await run_migrations(pool)
        async with pool.acquire() as connection:
            await connection.begin()
            try:
                await JobRepository(connection).release_expired_leases(
                    now=float(now_fn())
                )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise

        heartbeat = WorkerHeartbeatService(
            pool,
            now_fn=now_fn,
            lease_seconds=settings.job_lease_seconds,
        )
        while not stop.is_set():
            await heartbeat.touch(worker_id, "worker")
            try:
                await asyncio.wait_for(
                    stop.wait(),
                    timeout=settings.worker_heartbeat_seconds,
                )
            except TimeoutError:
                continue
    finally:
        stop.set()
        for signum in installed_signals:
            loop.remove_signal_handler(signum)
        await pool.close()


def main() -> int:
    try:
        asyncio.run(run_worker())
    except ConfigurationError as exc:
        print(f"FlyMail V2 worker configuration error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"FlyMail V2 worker failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
