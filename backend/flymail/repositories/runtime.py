"""SQL-only process runtime heartbeat access for FlyMail V2."""

from __future__ import annotations

import math
import time

import aiomysql


_ALLOWED_ROLES = {"api", "worker"}


def _required_text(value: str, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{label} is required")
    return normalized


def _role(value: str) -> str:
    normalized = _required_text(value, "role").casefold()
    if normalized not in _ALLOWED_ROLES:
        raise ValueError("unsupported process role")
    return normalized


def _timestamp(value: float | None) -> float:
    timestamp = float(time.time() if value is None else value)
    if not math.isfinite(timestamp) or timestamp < 0:
        raise ValueError("heartbeat timestamp must be finite and non-negative")
    return timestamp


class RuntimeRepository:
    """Persist process liveness without committing the caller transaction."""

    def __init__(self, connection: aiomysql.Connection) -> None:
        self.connection = connection

    async def touch_process(
        self,
        process_id: str,
        role: str,
        *,
        now: float | None = None,
    ) -> None:
        normalized_id = _required_text(process_id, "process_id")
        normalized_role = _role(role)
        timestamp = _timestamp(now)
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO process_heartbeats (
                    process_id, role, started_at, heartbeat_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s) AS incoming
                ON DUPLICATE KEY UPDATE
                    role = incoming.role,
                    heartbeat_at = incoming.heartbeat_at,
                    updated_at = incoming.updated_at
                """,
                (
                    normalized_id,
                    normalized_role,
                    timestamp,
                    timestamp,
                    timestamp,
                ),
            )

    async def latest_heartbeat(self, role: str) -> float | None:
        normalized_role = _role(role)
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                """
                SELECT heartbeat_at
                FROM process_heartbeats
                WHERE role = %s
                ORDER BY heartbeat_at DESC, process_id ASC
                LIMIT 1
                """,
                (normalized_role,),
            )
            row = await cursor.fetchone()
        return float(row[0]) if row else None
