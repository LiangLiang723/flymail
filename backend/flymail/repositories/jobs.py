"""SQL-only durable job repository with recoverable MySQL leases."""

from __future__ import annotations

import json
import math
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
    account_id: str | None = None
    provider_key: str | None = None
    priority: int = 100
    available_at: float = 0
    max_attempts: int = 10
    dedupe_key: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "queue_name", _required_text(self.queue_name, "queue_name"))
        object.__setattr__(self, "job_kind", _required_text(self.job_kind, "job_kind"))
        normalized_user = str(self.user_uid or "").strip() or None
        normalized_account = str(self.account_id or "").strip() or None
        normalized_provider = str(self.provider_key or "").strip().casefold() or None
        normalized_dedupe = str(self.dedupe_key or "").strip() or None
        if bool(normalized_account) != bool(normalized_provider):
            raise ValueError("account_id and provider_key must be supplied together")
        if normalized_account and not normalized_user:
            raise ValueError("account-scoped jobs require user_uid")
        object.__setattr__(self, "user_uid", normalized_user)
        object.__setattr__(self, "account_id", normalized_account)
        object.__setattr__(self, "provider_key", normalized_provider)
        object.__setattr__(self, "dedupe_key", normalized_dedupe)
        if not isinstance(self.payload, dict):
            raise ValueError("job payload must be an object")
        validate_safe_payload(self.payload, path="job.payload")
        object.__setattr__(self, "payload", dict(self.payload))
        if isinstance(self.priority, bool):
            raise TypeError("priority must be an integer")
        priority = int(self.priority)
        available_at = float(self.available_at)
        if not math.isfinite(available_at) or available_at < 0:
            raise ValueError("available_at must be a finite non-negative timestamp")
        if isinstance(self.max_attempts, bool) or int(self.max_attempts) < 1:
            raise ValueError("max_attempts must be at least 1")
        object.__setattr__(self, "priority", priority)
        object.__setattr__(self, "available_at", available_at)
        object.__setattr__(self, "max_attempts", int(self.max_attempts))


@dataclass(frozen=True, slots=True)
class LeasedJob:
    id: str
    user_uid: str | None
    account_id: str | None
    provider_key: str | None
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
class JobCandidate:
    id: str
    queue_name: str
    priority: int
    available_at: float
    account_id: str | None
    provider_key: str | None
    account_status: str
    runtime_status: str
    backoff_until: float


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
        if not math.isfinite(timestamp):
            raise ValueError("enqueue time must be finite")
        available_at = float(spec.available_at or timestamp)
        if spec.account_id:
            await self._validate_account_scope(spec)
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

    async def list_ready_candidates(
        self,
        queue_names: tuple[str, ...] | list[str],
        *,
        now: float | None = None,
        limit: int = 200,
        per_account_limit: int = 2,
        per_provider_limit: int = 2,
    ) -> list[JobCandidate]:
        normalized_queues = tuple(dict.fromkeys(_required_text(value, "queue_name") for value in queue_names))
        normalized_limit = int(limit)
        account_limit = int(per_account_limit)
        provider_limit = int(per_provider_limit)
        if not normalized_queues:
            raise ValueError("at least one queue is required")
        if normalized_limit < 1:
            raise ValueError("candidate limit must be at least 1")
        if account_limit < 1 or provider_limit < 1:
            raise ValueError("candidate scope limits must be at least 1")
        timestamp = float(time.time() if now is None else now)
        placeholders = ",".join("%s" for _ in normalized_queues)
        queue_order_placeholders = ",".join("%s" for _ in normalized_queues)
        async with self.connection.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                f"""
                WITH eligible AS (
                    SELECT w.id, w.queue_name, w.priority, w.available_at,
                           w.account_id, w.provider_key,
                           COALESCE(a.status, CASE WHEN w.account_id IS NULL THEN 'active' ELSE 'missing' END) AS account_status,
                           COALESCE(r.status, 'normal') AS runtime_status,
                           COALESCE(r.backoff_until, 0) AS backoff_until,
                           ROW_NUMBER() OVER (
                               PARTITION BY w.queue_name,
                                   CASE WHEN w.account_id IS NULL THEN CONCAT('job:', w.id)
                                        ELSE CONCAT('account:', w.account_id) END
                               ORDER BY w.priority ASC, w.available_at ASC, w.id ASC
                           ) AS account_rank,
                           ROW_NUMBER() OVER (
                               PARTITION BY w.queue_name,
                                   CASE WHEN w.provider_key = '' THEN CONCAT('job:', w.id)
                                        ELSE CONCAT('provider:', w.provider_key) END
                               ORDER BY w.priority ASC, w.available_at ASC, w.id ASC
                           ) AS provider_rank
                    FROM worker_jobs w FORCE INDEX (idx_worker_jobs_scheduler)
                    LEFT JOIN mail_accounts a
                      ON a.id = w.account_id AND a.user_uid = w.user_uid
                    LEFT JOIN users u ON u.id = w.user_uid
                    LEFT JOIN account_runtime_state r
                      ON r.account_id = w.account_id AND r.user_uid = w.user_uid
                    WHERE w.queue_name IN ({placeholders})
                      AND w.status IN ('pending', 'retry_wait')
                      AND w.available_at <= %s
                      AND (
                        w.account_id IS NULL OR (
                          a.id IS NOT NULL
                          AND u.enabled = 1
                          AND a.provider_key = w.provider_key
                          AND (
                            (
                              w.job_kind = 'account.verify'
                              AND a.status IN ('pending', 'auth_required', 'active')
                            ) OR (
                              w.job_kind <> 'account.verify'
                              AND a.status = 'active'
                              AND COALESCE(r.status, 'normal') NOT IN ('auth_required', 'disabled')
                              AND COALESCE(r.backoff_until, 0) <= %s
                            )
                          )
                        )
                      )
                ), diversified AS (
                    SELECT eligible.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY queue_name
                               ORDER BY priority ASC, available_at ASC, id ASC
                           ) AS queue_rank
                    FROM eligible
                    WHERE account_rank <= %s AND provider_rank <= %s
                )
                SELECT id, queue_name, priority, available_at,
                       account_id, provider_key, account_status,
                       runtime_status, backoff_until
                FROM diversified
                WHERE queue_rank <= %s
                ORDER BY FIELD(queue_name, {queue_order_placeholders}),
                         priority ASC, available_at ASC, id ASC
                """,
                (
                    *normalized_queues,
                    timestamp,
                    timestamp,
                    account_limit,
                    provider_limit,
                    normalized_limit,
                    *normalized_queues,
                ),
            )
            rows = [dict(row) for row in await cursor.fetchall()]
        return [
            JobCandidate(
                id=str(row["id"]),
                queue_name=str(row["queue_name"]),
                priority=int(row["priority"] or 0),
                available_at=float(row["available_at"] or 0),
                account_id=str(row["account_id"]) if row["account_id"] else None,
                provider_key=str(row["provider_key"]) if row["provider_key"] else None,
                account_status=str(row["account_status"] or "missing"),
                runtime_status=str(row["runtime_status"] or "normal"),
                backoff_until=float(row["backoff_until"] or 0),
            )
            for row in rows
        ]

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
        normalized_limit = int(limit)
        if normalized_limit < 1:
            raise ValueError("claim limit must be at least 1")
        timestamp = float(time.time() if now is None else now)
        async with self.connection.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                """
                SELECT w.id, w.user_uid, w.account_id, w.provider_key,
                       w.queue_name, w.job_kind, w.priority, w.available_at,
                       w.attempt_count, w.max_attempts, w.dedupe_key, w.payload
                FROM worker_jobs w FORCE INDEX (idx_worker_jobs_claim_order)
                LEFT JOIN mail_accounts a
                  ON a.id = w.account_id AND a.user_uid = w.user_uid
                LEFT JOIN users u ON u.id = w.user_uid
                LEFT JOIN account_runtime_state r
                  ON r.account_id = w.account_id AND r.user_uid = w.user_uid
                WHERE w.queue_name = %s
                  AND w.status IN ('pending', 'retry_wait')
                  AND w.available_at <= %s
                  AND (
                    w.account_id IS NULL OR (
                      a.id IS NOT NULL
                      AND u.enabled = 1
                      AND a.provider_key = w.provider_key
                      AND (
                        (
                          w.job_kind = 'account.verify'
                          AND a.status IN ('pending', 'auth_required', 'active')
                        ) OR (
                          w.job_kind <> 'account.verify'
                          AND a.status = 'active'
                          AND COALESCE(r.status, 'normal') NOT IN ('auth_required', 'disabled')
                          AND COALESCE(r.backoff_until, 0) <= %s
                        )
                      )
                    )
                  )
                ORDER BY w.priority ASC, w.available_at ASC, w.id ASC
                LIMIT %s
                FOR UPDATE SKIP LOCKED
                """,
                (normalized_queue, timestamp, timestamp, normalized_limit),
            )
            rows = [dict(row) for row in await cursor.fetchall()]
        return await self._lease_rows(rows, worker_id, lease_seconds, timestamp)

    async def claim_ids(
        self,
        job_ids: tuple[str, ...] | list[str],
        worker_id: str,
        *,
        lease_seconds: int,
        now: float | None = None,
    ) -> list[LeasedJob]:
        normalized_ids = tuple(dict.fromkeys(_required_text(value, "job_id") for value in job_ids))
        if not normalized_ids:
            return []
        timestamp = float(time.time() if now is None else now)
        placeholders = ",".join("%s" for _ in normalized_ids)
        async with self.connection.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                f"""
                SELECT w.id, w.user_uid, w.account_id, w.provider_key,
                       w.queue_name, w.job_kind, w.priority, w.available_at,
                       w.attempt_count, w.max_attempts, w.dedupe_key, w.payload
                FROM worker_jobs w
                LEFT JOIN mail_accounts a
                  ON a.id = w.account_id AND a.user_uid = w.user_uid
                LEFT JOIN users u ON u.id = w.user_uid
                LEFT JOIN account_runtime_state r
                  ON r.account_id = w.account_id AND r.user_uid = w.user_uid
                WHERE w.id IN ({placeholders})
                  AND w.status IN ('pending', 'retry_wait')
                  AND w.available_at <= %s
                  AND (
                    w.account_id IS NULL OR (
                      a.id IS NOT NULL
                      AND u.enabled = 1
                      AND a.provider_key = w.provider_key
                      AND (
                        (
                          w.job_kind = 'account.verify'
                          AND a.status IN ('pending', 'auth_required', 'active')
                        ) OR (
                          w.job_kind <> 'account.verify'
                          AND a.status = 'active'
                          AND COALESCE(r.status, 'normal') NOT IN ('auth_required', 'disabled')
                          AND COALESCE(r.backoff_until, 0) <= %s
                        )
                      )
                    )
                  )
                ORDER BY FIELD(w.id, {placeholders})
                FOR UPDATE SKIP LOCKED
                """,
                (*normalized_ids, timestamp, timestamp, *normalized_ids),
            )
            rows = [dict(row) for row in await cursor.fetchall()]
        return await self._lease_rows(rows, worker_id, lease_seconds, timestamp)

    async def _lease_rows(
        self,
        rows: list[Mapping[str, Any]],
        worker_id: str,
        lease_seconds: int,
        timestamp: float,
    ) -> list[LeasedJob]:
        normalized_worker = _required_text(worker_id, "worker_id")
        normalized_lease = int(lease_seconds)
        if normalized_lease < 1:
            raise ValueError("lease_seconds must be at least 1")
        lease_expires_at = timestamp + normalized_lease
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
                    (new_id("attempt"), row["id"], next_attempt, normalized_worker, timestamp),
                )
            leased.append(
                LeasedJob(
                    id=str(row["id"]),
                    user_uid=str(row["user_uid"]) if row["user_uid"] else None,
                    account_id=str(row["account_id"]) if row.get("account_id") else None,
                    provider_key=str(row["provider_key"]) if row.get("provider_key") else None,
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

    async def mark_running(
        self,
        job_id: str,
        lease_token: str,
        *,
        now: float | None = None,
    ) -> bool:
        timestamp = float(time.time() if now is None else now)
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                """
                UPDATE worker_jobs
                SET status = 'running', updated_at = %s
                WHERE id = %s AND lease_token = %s AND status = 'leased'
                """,
                (timestamp, _required_text(job_id, "job_id"), _required_text(lease_token, "lease_token")),
            )
            return cursor.rowcount == 1

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

    async def release_worker_leases(
        self,
        worker_id: str,
        *,
        now: float | None = None,
    ) -> int:
        normalized_worker = _required_text(worker_id, "worker_id")
        timestamp = float(time.time() if now is None else now)
        async with self.connection.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                """
                SELECT id, attempt_count, max_attempts
                FROM worker_jobs
                WHERE lease_owner = %s AND status IN ('leased', 'running')
                ORDER BY id ASC
                FOR UPDATE SKIP LOCKED
                """,
                (normalized_worker,),
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
                error_class="WorkerShutdown",
                error_message="worker stopped before job completion",
            )
            async with self.connection.cursor() as cursor:
                await cursor.execute(
                    """
                    UPDATE worker_jobs
                    SET status = %s, available_at = %s, lease_owner = '',
                        lease_token = NULL, lease_expires_at = NULL,
                        heartbeat_at = NULL, last_error_class = 'WorkerShutdown',
                        last_error_message = 'worker stopped before job completion',
                        updated_at = %s, finished_at = %s
                    WHERE id = %s AND lease_owner = %s
                      AND status IN ('leased', 'running')
                    """,
                    (
                        next_status,
                        timestamp,
                        timestamp,
                        timestamp if terminal else None,
                        row["id"],
                        normalized_worker,
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

    async def has_uncertain_send(
        self,
        user_uid: str,
        account_id: str,
    ) -> bool:
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                """
                SELECT (
                    EXISTS(
                        SELECT 1
                        FROM worker_jobs
                        WHERE user_uid = %s AND account_id = %s
                          AND job_kind = 'send.verify'
                          AND status IN ('pending', 'retry_wait', 'leased', 'running')
                    ) OR EXISTS(
                        SELECT 1
                        FROM drafts
                        WHERE user_uid = %s AND account_id = %s
                          AND send_state IN ('verification_required', 'review_required')
                    )
                )
                """,
                (
                    _required_text(user_uid, "user_uid"),
                    _required_text(account_id, "account_id"),
                    _required_text(user_uid, "user_uid"),
                    _required_text(account_id, "account_id"),
                ),
            )
            row = await cursor.fetchone()
        return bool(row and row[0])

    async def cancel_pending_non_send_for_account(
        self,
        user_uid: str,
        account_id: str,
        *,
        now: float | None = None,
    ) -> int:
        timestamp = float(time.time() if now is None else now)
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                """
                UPDATE worker_jobs
                SET status = 'cancelled', finished_at = %s,
                    updated_at = %s, lease_owner = '', lease_token = NULL,
                    lease_expires_at = NULL, heartbeat_at = NULL,
                    last_error_class = '', last_error_message = ''
                WHERE user_uid = %s AND account_id = %s
                  AND status IN ('pending', 'retry_wait')
                  AND job_kind NOT LIKE 'send.%%'
                """,
                (
                    timestamp,
                    timestamp,
                    _required_text(user_uid, "user_uid"),
                    _required_text(account_id, "account_id"),
                ),
            )
            return int(cursor.rowcount or 0)

    async def _validate_account_scope(self, spec: JobSpec) -> None:
        if spec.account_id is None or spec.provider_key is None or spec.user_uid is None:
            return
        async with self.connection.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                """
                SELECT provider_key
                FROM mail_accounts
                WHERE id = %s AND user_uid = %s
                """,
                (spec.account_id, spec.user_uid),
            )
            row = await cursor.fetchone()
        if not row:
            raise ValueError("job account does not belong to user")
        if str(row["provider_key"] or "").strip().casefold() != spec.provider_key:
            raise ValueError("job provider does not match account")

    async def _get_deduped_for_update(self, queue_name: str, dedupe_key: str) -> Mapping[str, Any] | None:
        async with self.connection.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                """
                SELECT id, status, user_uid, account_id, provider_key, job_kind
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
                    id, user_uid, account_id, provider_key, queue_name,
                    job_kind, status, priority, available_at, lease_owner,
                    lease_token, lease_expires_at, heartbeat_at, attempt_count,
                    max_attempts, dedupe_key, payload, last_error_class,
                    last_error_message, created_at, updated_at, finished_at
                ) VALUES (%s, %s, %s, %s, %s, %s, 'pending', %s, %s, '', NULL,
                          NULL, NULL, 0, %s, %s, %s, '', '', %s, %s, NULL)
                """,
                (
                    job_id,
                    spec.user_uid,
                    spec.account_id,
                    spec.provider_key or "",
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
        existing_scope = (
            str(existing["user_uid"]) if existing.get("user_uid") else None,
            str(existing["account_id"]) if existing.get("account_id") else None,
            str(existing["provider_key"] or "").strip().casefold() or None,
            str(existing["job_kind"] or ""),
        )
        requested_scope = (
            spec.user_uid,
            spec.account_id,
            spec.provider_key,
            spec.job_kind,
        )
        if existing_scope != requested_scope:
            raise ValueError("dedupe key belongs to a different job scope")
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
