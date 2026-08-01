"""Persistent login failure windows for FlyMail V2."""

from __future__ import annotations

from dataclasses import dataclass

import aiomysql


@dataclass(frozen=True, slots=True)
class LoginFailureWindow:
    failure_count: int
    window_started_at: float
    blocked_until: float


class LoginRateLimitRepository:
    """Manage one hashed principal/source window on the caller transaction."""

    def __init__(self, connection: aiomysql.Connection) -> None:
        self.connection = connection

    async def lock_window(
        self,
        principal_hash: str,
        source_hash: str,
        *,
        now: float,
    ) -> LoginFailureWindow:
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO login_rate_limits (
                    principal_hash, source_hash, failure_count,
                    window_started_at, blocked_until, updated_at
                ) VALUES (%s, %s, 0, %s, 0, %s)
                AS incoming
                ON DUPLICATE KEY UPDATE updated_at = incoming.updated_at
                """,
                (principal_hash, source_hash, float(now), float(now)),
            )
        async with self.connection.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                """
                SELECT failure_count, window_started_at, blocked_until
                FROM login_rate_limits
                WHERE principal_hash = %s AND source_hash = %s
                FOR UPDATE
                """,
                (principal_hash, source_hash),
            )
            row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("login failure window was not created")
        return LoginFailureWindow(
            failure_count=int(row["failure_count"] or 0),
            window_started_at=float(row["window_started_at"] or 0),
            blocked_until=float(row["blocked_until"] or 0),
        )

    async def record_failure(
        self,
        principal_hash: str,
        source_hash: str,
        *,
        failure_count: int,
        window_started_at: float,
        blocked_until: float,
        now: float,
    ) -> None:
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                """
                UPDATE login_rate_limits
                SET failure_count = %s, window_started_at = %s,
                    blocked_until = %s, updated_at = %s
                WHERE principal_hash = %s AND source_hash = %s
                """,
                (
                    int(failure_count),
                    float(window_started_at),
                    float(blocked_until),
                    float(now),
                    principal_hash,
                    source_hash,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("login failure window update was lost")

    async def clear(self, principal_hash: str, source_hash: str) -> None:
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                """
                DELETE FROM login_rate_limits
                WHERE principal_hash = %s AND source_hash = %s
                """,
                (principal_hash, source_hash),
            )
