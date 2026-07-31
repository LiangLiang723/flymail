"""SQL-only durable job repository with recoverable MySQL leases."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Mapping

import aiomysql
import pymysql

from flymail.domain.ids import new_id
from flymail.repositories.outbox import encode_safe_json, validate_safe_payload


_ACTIVE_STATUSES = {"pending", "leased", "running", "retry_wait"}
_TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}


def _required_text(value: str, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{label} is required")
    return normalized


def _safe_error(value: str) -> str:
    return str(value or "").replace("\x00", "")[:512]


def _decode_json(value) -> dict:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    decoded = json.loads(value)
    return dict(decoded) if isinstance(decoded, dict) else {}


def retry_delay_seconds(
    attempt: int,
    base_seconds: int,
    max_seconds: int,
    deterministic_jitter_seconds: int = 0,
) -> int:
    normalized_attempt = max(int(attempt), 1)
    normalized_base = int(base_seconds)
    normalized_max = int(max_seconds)
    jitter = int(deterministic_jitter_seconds)
    if normalized_base < 0 or normalized_max < 0 or jitter < 0:
        raise ValueError("retry delay values must be non-negative")
    exponent = max(normalized_attempt - 1, 0)
    if normalized_base == 0 or normalized_max == 0:
        bounded = 0
    elif exponent >= normalized_max.bit_length():
        bounded = normalized_max
    else:
        bounded = min(normalized_max, normalized_base << exponent)
    return bounded + jitter


@dataclass(frozen=True, slots=True)
class JobSpec:
    queue_name: str
    job_kind: str
    payload: dict
    user_uid: str | None = None
    priority: int = 100
    available_at: float = 0
    max_attempts: int = 10
    dedupe_key: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "queue_name", _required_text(self.queue_name, "queue_name"))
        object.__setattr__(self, "job_kind", _required_text(self.job_kind, "job_kind"))
        normalized_user = str(self.user_uid or "").strip() or None
        normalized_dedupe = str(self.dedupe_key or "").strip() or None
        object.__setattr__(self, "user_uid", normalized_user)
        object.__setattr__(self, "dedupe_key", normalized_dedupe)
        if not isinstance(self.payload, dict):
            raise ValueError("job payload must be an object")
        validate_safe_payload(self.payload, path="job.payload")
        object.__setattr__(self, "payload", dict(self.payload))
        if int(self.max_attempts) < 1:
            raise ValueError("max_attempts must be at least 1")


@dataclass(frozen=True, slots=True)
class LeasedJob:
    id: str
    user_uid: str | None
    queue_name: str
    job_kind: str
    priority: int
    available_at: float
    lease_owner: str
    lease_token: str = field(repr=False)
    lease_expires_at: float
    attempt_count: int
    max_attempts: int
    dedupe_key: str | None
    payload: dict


@dataclass(frozen=True, slots=True)
class _CurrentLease:
    attempt_count: int
    max_attempts: int
    lease_owner: str


class JobRepository:
    """Persist and lease jobs on the caller-owned transaction."""

    def __init__(self, connection: aiomysql.Connection) -> None:
        self.connection = connection

    async def enqueue(self, spec: JobSpec, *, now: float | None = None) -> str:
        timestamp = float(time.time() if now is None else now)
        available_at = float(spec.available_at or timestamp)
        if spec.dedupe_key:
            existing = await self._get_deduped_for_update(spec.queue_name, spec.dedupe_key)
            if existing:
                return await self._reuse_or_supersede(existing, spec, timestamp, available_at)

        try:
            return await self._insert_new(spec, timestamp, available_at)
        except pymysql.err.IntegrityError as exc:
            if int(exc.args[0] or 0) != 1062 or not spec.dedupe_key:
                raise
            existing = await self._get_deduped_for_update(spec.queue_name, spec.dedupe_key)
            if not existing:
                raise
            return await self._reuse_or_supersede(existing, spec, timestamp, available_at)

    async def claim(
        self,
        queue_name: str,
        worker_id: str,
        limit: int,
        lease_seconds: int,
        *,
        now: float | None = None,
    ) -> list[LeasedJob]:
        normalized_queue = _required_text(queue_name, "queue_name")
        normalized_worker = _required_text(worker_id, "worker_id")
        normalized_limit = int(limit)
        normalized_lease = int(lease_seconds)
        if normalized_limit < 1:
            raise ValueError("claim limit must be at least 1")
        if normalized_lease < 1:
            raise ValueError("lease_seconds must be at least 1")
        timestamp = float(time.time() if now is None else now)
        lease_expires_at = timestamp + normalized_lease

        async with self.connection.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                """
                SELECT id, user_uid, queue_name, job_kind, priority, available_at,
                       attempt_count, max_attempts, dedupe_key, payload
                FROM worker_jobs FORCE INDEX (idx_worker_jobs_claim_order)
                WHERE queue_name = %s
                  AND status IN ('pending', 'retry_wait')
                  AND available_at <= %s
                ORDER BY priority ASC, available_at ASC, id ASC
                LIMIT %s
                FOR UPDATE SKIP LOCKED
                """,
                (normalized_queue, timestamp, normalized_limit),
            )
            rows = [dict(row) for row in await cursor.fetchall()]

        leased: list[LeasedJob] = []
        for row in rows:
            lease_token = new_id("lease")
            next_attempt = int(row["attempt_count"] or 0) + 1
            async with self.connection.cursor() as cursor:
                await cursor.execute(
                    """
                    UPDATE worker_jobs
                    SET status = 'leased', lease_owner = %s, lease_token = %s,
                        lease_expires_at = %s, heartbeat_at = %s,
                        attempt_count = %s, updated_at = %s, finished_at = NULL,
                        last_error_class = '', last_error_message = ''
                    WHERE id = %s
                      AND status IN ('pending', 'retry_wait')
                      AND available_at <= %s
                    """,
                    (
                        normalized_worker,
                        lease_token,
                        lease_expires_at,
                        timestamp,
                        next_attempt,
                        timestamp,
                        row["id"],
                        timestamp,
                    ),
                )
                if cursor.rowcount != 1:
                    continue
                await cursor.execute(
                    """
                    INSERT INTO job_attempts (
                        id, job_id, attempt_number, worker_id, started_at,
                        finished_at, outcome, error_class, error_message,
                        duration_ms, safe_metadata
                    ) VALUES (%s, %s, %s, %s, %s, NULL, 'running', '', '', 0, NULL)
                    """,
                    (
                        new_id("attempt"),
                        row["id"],
                        next_attempt,
                        normalized_worker,
                        timestamp,
                    ),
                )
            leased.append(
                LeasedJob(
                    id=str(row["id"]),
                    user_uid=str(row["user_uid"]) if row["user_uid"] else None,
                    queue_name=str(row["queue_name"]),
                    job_kind=str(row["job_kind"]),
                    priority=int(row["priority"] or 0),
                    available_at=float(row["available_at"] or 0),
                    lease_owner=normalized_worker,
                    lease_token=lease_token,
                    lease_expires_at=lease_expires_at,
                    attempt_count=next_attempt,
                    max_attempts=int(row["max_attempts"] or 1),
                    dedupe_key=str(row["dedupe_key"]) if row["dedupe_key"] else None,
                    payload=_decode_json(row["payload"]),
                )
            )
        return leased

    async def heartbeat(
        self,
        job_id: str,
        lease_token: str,
        extend_seconds: int,
        *,
        now: float | None = None,
    ) -> bool:
        extension = int(extend_seconds)
        if extension < 1:
            raise ValueError("extend_seconds must be at least 1")
        timestamp = float(time.time() if now is None else now)
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                """
                UPDATE worker_jobs
                SET heartbeat_at = %s, lease_expires_at = %s, updated_at = %s
                WHERE id = %s AND lease_token = %s
                  AND status IN ('leased', 'running')
                """,
                (
                    timestamp,
                    timestamp + extension,
                    timestamp,
                    _required_text(job_id, "job_id"),
                    _required_text(lease_token, "lease_token"),
                ),
            )
            return cursor.rowcount == 1

    async def complete(
        self,
        job_id: str,
        lease_token: str,
        *,
        now: float | None = None,
    ) -> bool:
        timestamp = float(time.time() if now is None else now)
        current = await self._current_lease(job_id, lease_token)
        if current is None:
            return False
        await self._finish_attempt(job_id, current.attempt_count, "succeeded", timestamp)
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                """
                UPDATE worker_jobs
                SET status = 'succeeded', lease_owner = '', lease_token = NULL,
                    lease_expires_at = NULL, heartbeat_at = NULL,
                    updated_at = %s, finished_at = %s,
                    last_error_class = '', last_error_message = ''
                WHERE id = %s AND lease_token = %s
                  AND status IN ('leased', 'running')
                """,
                (timestamp, timestamp, job_id, lease_token),
            )
            return cursor.rowcount == 1

    async def retry(
        self,
        job_id: str,
        lease_token: str,
        *,
        error_class: str,
        error_message: str,
        base_seconds: int,
        max_seconds: int,
        jitter_seconds: int = 0,
        now: float | None = None,
    ) -> bool:
        timestamp = float(time.time() if now is None else now)
        current = await self._current_lease(job_id, lease_token)
        if current is None:
            return False
        terminal = current.attempt_count >= current.max_attempts
        outcome = "failed" if terminal else "retry"
        await self._finish_attempt(
            job_id,
            current.attempt_count,
            outcome,
            timestamp,
            error_class=error_class,
            error_message=error_message,
        )
        next_status = "failed" if terminal else "retry_wait"
        available_at = timestamp
        finished_at: float | None = timestamp
        if not terminal:
            available_at += retry_delay_seconds(
                current.attempt_count,
                base_seconds,
                max_seconds,
                jitter_seconds,
            )
            finished_at = None
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                """
                UPDATE worker_jobs
                SET status = %s, available_at = %s, lease_owner = '',
                    lease_token = NULL, lease_expires_at = NULL, heartbeat_at = NULL,
                    last_error_class = %s, last_error_message = %s,
                    updated_at = %s, finished_at = %s
                WHERE id = %s AND lease_token = %s
                  AND status IN ('leased', 'running')
                """,
                (
                    next_status,
                    available_at,
                    _safe_error(error_class),
                    _safe_error(error_message),
                    timestamp,
                    finished_at,
                    job_id,
                    lease_token,
                ),
            )
            return cursor.rowcount == 1

    async def fail(
        self,
        job_id: str,
        lease_token: str,
        *,
        error_class: str,
        error_message: str,
        now: float | None = None,
    ) -> bool:
        timestamp = float(time.time() if now is None else now)
        current = await self._current_lease(job_id, lease_token)
        if current is None:
            return False
        await self._finish_attempt(
            job_id,
            current.attempt_count,
            "failed",
            timestamp,
            error_class=error_class,
            error_message=error_message,
        )
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                """
                UPDATE worker_jobs
                SET status = 'failed', lease_owner = '', lease_token = NULL,
                    lease_expires_at = NULL, heartbeat_at = NULL,
                    last_error_class = %s, last_error_message = %s,
                    updated_at = %s, finished_at = %s
                WHERE id = %s AND lease_token = %s
                  AND status IN ('leased', 'running')
                """,
                (
                    _safe_error(error_class),
                    _safe_error(error_message),
                    timestamp,
                    timestamp,
                    job_id,
                    lease_token,
                ),
            )
            return cursor.rowcount == 1

    async def release_expired_leases(self, *, now: float | None = None) -> int:
        timestamp = float(time.time() if now is None else now)
        async with self.connection.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                """
                SELECT id, attempt_count, max_attempts
                FROM worker_jobs
                WHERE status IN ('leased', 'running')
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at <= %s
                ORDER BY lease_expires_at ASC, id ASC
                FOR UPDATE SKIP LOCKED
                """,
                (timestamp,),
            )
            rows = [dict(row) for row in await cursor.fetchall()]

        released = 0
        for row in rows:
            terminal = int(row["attempt_count"] or 0) >= int(row["max_attempts"] or 1)
            next_status = "failed" if terminal else "retry_wait"
            await self._finish_attempt(
                str(row["id"]),
                int(row["attempt_count"] or 0),
                "failed" if terminal else "retry",
                timestamp,
                error_class="LeaseExpired",
                error_message="worker lease expired",
            )
            async with self.connection.cursor() as cursor:
                await cursor.execute(
                    """
                    UPDATE worker_jobs
                    SET status = %s, available_at = %s, lease_owner = '',
                        lease_token = NULL, lease_expires_at = NULL,
                        heartbeat_at = NULL, last_error_class = 'LeaseExpired',
                        last_error_message = 'worker lease expired',
                        updated_at = %s, finished_at = %s
                    WHERE id = %s AND status IN ('leased', 'running')
                      AND lease_expires_at <= %s
                    """,
                    (
                        next_status,
                        timestamp,
                        timestamp,
                        timestamp if terminal else None,
                        row["id"],
                        timestamp,
                    ),
                )
                released += int(cursor.rowcount or 0)
        return released

    async def touch_worker_jobs(
        self,
        worker_id: str,
        *,
        lease_seconds: int,
        now: float | None = None,
    ) -> int:
        normalized_worker = _required_text(worker_id, "worker_id")
        extension = int(lease_seconds)
        if extension < 1:
            raise ValueError("lease_seconds must be at least 1")
        timestamp = float(time.time() if now is None else now)
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                """
                UPDATE worker_jobs
                SET heartbeat_at = %s,
                    lease_expires_at = GREATEST(COALESCE(lease_expires_at, 0), %s),
                    updated_at = %s
                WHERE lease_owner = %s
                  AND status IN ('leased', 'running')
                """,
                (timestamp, timestamp + extension, timestamp, normalized_worker),
            )
            return int(cursor.rowcount or 0)

    async def _get_deduped_for_update(self, queue_name: str, dedupe_key: str) -> Mapping[str, Any] | None:
        async with self.connection.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                """
                SELECT id, status
                FROM worker_jobs
                WHERE queue_name = %s AND dedupe_key = %s
                FOR UPDATE
                """,
                (queue_name, dedupe_key),
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def _insert_new(
        self,
        spec: JobSpec,
        timestamp: float,
        available_at: float,
    ) -> str:
        job_id = new_id("job")
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO worker_jobs (
                    id, user_uid, queue_name, job_kind, status, priority,
                    available_at, lease_owner, lease_token, lease_expires_at,
                    heartbeat_at, attempt_count, max_attempts, dedupe_key,
                    payload, last_error_class, last_error_message,
                    created_at, updated_at, finished_at
                ) VALUES (%s, %s, %s, %s, 'pending', %s, %s, '', NULL,
                          NULL, NULL, 0, %s, %s, %s, '', '', %s, %s, NULL)
                """,
                (
                    job_id,
                    spec.user_uid,
                    spec.queue_name,
                    spec.job_kind,
                    int(spec.priority),
                    available_at,
                    int(spec.max_attempts),
                    spec.dedupe_key,
                    encode_safe_json(spec.payload),
                    timestamp,
                    timestamp,
                ),
            )
        return job_id

    async def _reuse_or_supersede(
        self,
        existing: Mapping[str, Any],
        spec: JobSpec,
        timestamp: float,
        available_at: float,
    ) -> str:
        job_id = str(existing["id"])
        status = str(existing["status"])
        if status in _ACTIVE_STATUSES or status not in _TERMINAL_STATUSES:
            return job_id
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                """
                UPDATE worker_jobs
                SET dedupe_key = NULL, updated_at = %s
                WHERE id = %s AND status IN ('succeeded', 'failed', 'cancelled')
                """,
                (timestamp, job_id),
            )
        return await self._insert_new(spec, timestamp, available_at)

    async def _current_lease(self, job_id: str, lease_token: str) -> _CurrentLease | None:
        normalized_job = _required_text(job_id, "job_id")
        normalized_token = _required_text(lease_token, "lease_token")
        async with self.connection.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                """
                SELECT attempt_count, max_attempts, lease_owner
                FROM worker_jobs
                WHERE id = %s AND lease_token = %s
                  AND status IN ('leased', 'running')
                FOR UPDATE
                """,
                (normalized_job, normalized_token),
            )
            row = await cursor.fetchone()
        if not row:
            return None
        return _CurrentLease(
            attempt_count=int(row["attempt_count"] or 0),
            max_attempts=int(row["max_attempts"] or 1),
            lease_owner=str(row["lease_owner"] or ""),
        )

    async def _finish_attempt(
        self,
        job_id: str,
        attempt_number: int,
        outcome: str,
        timestamp: float,
        *,
        error_class: str = "",
        error_message: str = "",
    ) -> None:
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                """
                UPDATE job_attempts
                SET finished_at = %s, outcome = %s,
                    error_class = %s, error_message = %s,
                    duration_ms = GREATEST(ROUND((%s - started_at) * 1000), 0)
                WHERE job_id = %s AND attempt_number = %s
                  AND outcome = 'running'
                """,
                (
                    timestamp,
                    outcome,
                    _safe_error(error_class),
                    _safe_error(error_message),
                    timestamp,
                    job_id,
                    int(attempt_number),
                ),
            )
