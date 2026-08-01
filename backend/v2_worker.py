"""FlyMail V2 durable Worker process and graceful job execution lifecycle."""

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
from flymail.providers.registry import ProviderRegistry
from flymail.repositories.jobs import JobCandidate, JobRepository, LeasedJob
from flymail.workers.dispatcher import JobOutcome, WorkerDispatcher
from flymail.workers.lease import WorkerHeartbeatService
from flymail.workers.scheduler import (
    QUEUE_ORDER,
    ClaimRequest,
    FairScheduler,
    ReadyJob,
)


async def _wait_for_stop(stop_event: asyncio.Event, timeout: float) -> None:
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=timeout)
    except TimeoutError:
        return


def _ready_job(candidate: JobCandidate) -> ReadyJob:
    return ReadyJob(
        id=candidate.id,
        queue_name=candidate.queue_name,
        priority=candidate.priority,
        available_at=candidate.available_at,
        account_id=candidate.account_id,
        provider_key=candidate.provider_key,
        account_status=candidate.account_status,
        runtime_status=candidate.runtime_status,
        backoff_until=candidate.backoff_until,
    )


async def _finish_job(
    pool: DatabasePool,
    dispatcher: WorkerDispatcher,
    job: LeasedJob,
    stop_event: asyncio.Event,
    now_fn: Callable[[], float],
) -> None:
    async with pool.acquire() as connection:
        await connection.begin()
        try:
            running = await JobRepository(connection).mark_running(
                job.id,
                job.lease_token,
                now=float(now_fn()),
            )
            await connection.commit()
        except Exception:
            await connection.rollback()
            raise
    if not running:
        return

    outcome = await dispatcher.dispatch(job, stop_event=stop_event)
    if not isinstance(outcome, JobOutcome):
        outcome = JobOutcome.fail("InvalidJobOutcome")
    async with pool.acquire() as connection:
        await connection.begin()
        repository = JobRepository(connection)
        try:
            timestamp = float(now_fn())
            if outcome.action == "complete":
                await repository.complete(job.id, job.lease_token, now=timestamp)
            elif outcome.action == "retry":
                await repository.retry(
                    job.id,
                    job.lease_token,
                    error_class=outcome.error_class,
                    error_message=outcome.error_message,
                    base_seconds=outcome.retry_base_seconds,
                    max_seconds=outcome.retry_max_seconds,
                    jitter_seconds=outcome.retry_jitter_seconds,
                    now=timestamp,
                )
            else:
                await repository.fail(
                    job.id,
                    job.lease_token,
                    error_class=outcome.error_class,
                    error_message=outcome.error_message,
                    now=timestamp,
                )
            await connection.commit()
        except Exception:
            await connection.rollback()
            raise


async def _heartbeat_loop(
    heartbeat: WorkerHeartbeatService,
    worker_id: str,
    stop_event: asyncio.Event,
    interval_seconds: float,
) -> None:
    while not stop_event.is_set():
        await heartbeat.touch(worker_id, "worker")
        await _wait_for_stop(stop_event, interval_seconds)


async def _lease_reaper_loop(
    pool: DatabasePool,
    stop_event: asyncio.Event,
    now_fn: Callable[[], float],
    interval_seconds: float,
) -> None:
    while not stop_event.is_set():
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
        await _wait_for_stop(stop_event, interval_seconds)


async def _claim_loop(
    pool: DatabasePool,
    dispatcher: WorkerDispatcher,
    scheduler: FairScheduler,
    worker_id: str,
    stop_event: asyncio.Event,
    now_fn: Callable[[], float],
    lease_seconds: int,
    poll_seconds: float,
    active_tasks: dict[str, asyncio.Task[None]],
    active_claims: dict[str, ClaimRequest],
    fatal_errors: list[BaseException],
) -> None:
    while not stop_event.is_set():
        available_slots = max(scheduler.global_slots - len(active_tasks), 0)
        if available_slots > 0:
            timestamp = float(now_fn())
            async with pool.acquire() as connection:
                candidates = await JobRepository(connection).list_ready_candidates(
                    QUEUE_ORDER,
                    now=timestamp,
                    limit=max(64, available_slots * 8),
                    per_account_limit=scheduler.per_account_limit,
                    per_provider_limit=max(
                        scheduler.provider_limits.values(),
                        default=scheduler.global_slots,
                    ),
                )
            requests = scheduler.next_claims(
                tuple(_ready_job(candidate) for candidate in candidates),
                in_flight=tuple(active_claims.values()),
                now=timestamp,
            )
            if requests:
                async with pool.acquire() as connection:
                    await connection.begin()
                    try:
                        leased = await JobRepository(connection).claim_ids(
                            tuple(request.job_id for request in requests),
                            worker_id,
                            lease_seconds=lease_seconds,
                            now=timestamp,
                        )
                        await connection.commit()
                    except Exception:
                        await connection.rollback()
                        raise
                request_by_id = {request.job_id: request for request in requests}
                for job in leased:
                    if stop_event.is_set():
                        break
                    claim = request_by_id[job.id]
                    task = asyncio.create_task(
                        _finish_job(pool, dispatcher, job, stop_event, now_fn),
                        name=f"flymail-job-{job.id}",
                    )
                    active_tasks[job.id] = task
                    active_claims[job.id] = claim

                    def on_done(completed: asyncio.Task[None], job_id: str = job.id) -> None:
                        active_tasks.pop(job_id, None)
                        active_claims.pop(job_id, None)
                        try:
                            completed.result()
                        except asyncio.CancelledError:
                            return
                        except Exception as exc:
                            fatal_errors.append(exc)
                            stop_event.set()

                    task.add_done_callback(on_done)
        await _wait_for_stop(stop_event, poll_seconds)


async def _release_worker_leases(
    pool: DatabasePool,
    worker_id: str,
    now_fn: Callable[[], float],
) -> None:
    async with pool.acquire() as connection:
        await connection.begin()
        try:
            await JobRepository(connection).release_worker_leases(
                worker_id,
                now=float(now_fn()),
            )
            await connection.commit()
        except Exception:
            await connection.rollback()
            raise


async def _drain_active_tasks(
    active_tasks: dict[str, asyncio.Task[None]],
    *,
    grace_seconds: float,
    fatal_errors: list[BaseException],
) -> None:
    tasks = tuple(active_tasks.values())
    if not tasks:
        return
    _done, pending = await asyncio.wait(tasks, timeout=grace_seconds)
    for task in pending:
        task.cancel()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for result in results:
        if isinstance(result, asyncio.CancelledError):
            continue
        if isinstance(result, BaseException) and all(result is not item for item in fatal_errors):
            fatal_errors.append(result)


def _default_scheduler() -> FairScheduler:
    registry = ProviderRegistry.default()
    provider_limits = {
        key: registry.get(key).capabilities().max_parallel_connections
        for key in registry.keys()
    }
    return FairScheduler(
        global_slots=8,
        per_account_limit=2,
        provider_limits=provider_limits,
    )


async def run_worker(
    *,
    stop_event: asyncio.Event | None = None,
    now_fn: Callable[[], float] = time.time,
    dispatcher: WorkerDispatcher | None = None,
    scheduler: FairScheduler | None = None,
    poll_seconds: float = 0.25,
    shutdown_grace_seconds: float = 30,
) -> None:
    if float(poll_seconds) <= 0:
        raise ValueError("poll_seconds must be positive")
    if float(shutdown_grace_seconds) < 0:
        raise ValueError("shutdown_grace_seconds must be non-negative")
    settings = FlyMailSettings.from_env("worker")
    pool = await DatabasePool.create(settings)
    worker_id = new_id("wrk")
    stop = stop_event or asyncio.Event()
    runtime_dispatcher = dispatcher or WorkerDispatcher()
    runtime_scheduler = scheduler or _default_scheduler()
    installed_signals: list[signal.Signals] = []
    loop = asyncio.get_running_loop()
    active_tasks: dict[str, asyncio.Task[None]] = {}
    active_claims: dict[str, ClaimRequest] = {}
    fatal_errors: list[BaseException] = []
    migrations_ready = False
    runtime_error: BaseException | None = None
    cleanup_error: BaseException | None = None

    if stop_event is None:
        for signum in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(signum, stop.set)
                installed_signals.append(signum)
            except (NotImplementedError, RuntimeError):
                continue

    try:
        await run_migrations(pool)
        migrations_ready = True
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
        reaper_interval = max(
            1.0,
            min(
                float(settings.job_lease_seconds) / 2,
                float(settings.worker_heartbeat_seconds) * 3,
            ),
        )
        async with asyncio.TaskGroup() as group:
            group.create_task(
                _heartbeat_loop(
                    heartbeat,
                    worker_id,
                    stop,
                    float(settings.worker_heartbeat_seconds),
                )
            )
            group.create_task(
                _lease_reaper_loop(pool, stop, now_fn, reaper_interval)
            )
            group.create_task(
                _claim_loop(
                    pool,
                    runtime_dispatcher,
                    runtime_scheduler,
                    worker_id,
                    stop,
                    now_fn,
                    settings.job_lease_seconds,
                    float(poll_seconds),
                    active_tasks,
                    active_claims,
                    fatal_errors,
                )
            )
            await stop.wait()
    except BaseException as exc:
        runtime_error = exc
    finally:
        stop.set()
        try:
            await _drain_active_tasks(
                active_tasks,
                grace_seconds=float(shutdown_grace_seconds),
                fatal_errors=fatal_errors,
            )
            if migrations_ready:
                await _release_worker_leases(pool, worker_id, now_fn)
        except BaseException as exc:
            cleanup_error = exc
        for task in tuple(active_tasks.values()):
            if not task.done():
                task.cancel()
        if active_tasks:
            await asyncio.gather(*tuple(active_tasks.values()), return_exceptions=True)
        for signum in installed_signals:
            loop.remove_signal_handler(signum)
        try:
            await pool.close()
        except BaseException as exc:
            if cleanup_error is None:
                cleanup_error = exc

    if runtime_error is not None:
        raise runtime_error
    if fatal_errors:
        raise fatal_errors[0]
    if cleanup_error is not None:
        raise cleanup_error


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
