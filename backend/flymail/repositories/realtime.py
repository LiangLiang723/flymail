"""Persisted tenant-scoped realtime event stream."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

import aiomysql

from flymail.domain.ids import new_id
from flymail.repositories.base import TenantContext


@dataclass(frozen=True, slots=True)
class RealtimeEvent:
    sequence: int
    event_type: str
    aggregate_id: str | None
    occurred_at: float
    payload: dict[str, Any]


class RealtimeRepository:
    EVENT_TYPES = frozenset(
        {
            "thread.created",
            "thread.updated",
            "thread.removed",
            "message.body_state",
            "operation.updated",
            "send.updated",
            "account.status_changed",
            "sync.updated",
            "conflict.created",
            "settings.updated",
            "session.revoked",
            "version.changed",
            "notification.created",
            "notification.updated",
        }
    )
    _DENIED_KEYS = frozenset(
        {
            "body",
            "body_html",
            "body_text",
            "attachment",
            "attachment_bytes",
            "credential",
            "credentials",
            "password",
            "token",
            "access_token",
            "refresh_token",
            "recipients",
            "to",
            "cc",
            "bcc",
            "ciphertext",
            "nonce",
        }
    )

    def __init__(self, connection: aiomysql.Connection) -> None:
        self.connection = connection

    @classmethod
    def _validate_payload(cls, value: object, *, depth: int = 0) -> None:
        if depth > 4:
            raise ValueError("realtime payload is too deeply nested")
        if isinstance(value, Mapping):
            for key, item in value.items():
                normalized = str(key).casefold()
                if normalized in cls._DENIED_KEYS:
                    raise ValueError("realtime payload contains sensitive fields")
                cls._validate_payload(item, depth=depth + 1)
        elif isinstance(value, (list, tuple)):
            if len(value) > 50:
                raise ValueError("realtime payload list is too large")
            for item in value:
                cls._validate_payload(item, depth=depth + 1)
        elif not isinstance(value, (str, int, float, bool, type(None))):
            raise ValueError("realtime payload contains unsupported values")

    async def append(
        self,
        tenant: TenantContext,
        *,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str | None,
        payload: Mapping[str, object],
        now: float,
        expires_at: float,
    ) -> int:
        normalized_type = str(event_type or "").strip()
        if normalized_type not in self.EVENT_TYPES:
            raise ValueError("unsupported realtime event type")
        if float(expires_at) <= float(now):
            raise ValueError("realtime event expiry must be in the future")
        safe_payload = dict(payload)
        self._validate_payload(safe_payload)
        encoded = json.dumps(safe_payload, ensure_ascii=False, sort_keys=True)
        if len(encoded.encode("utf-8")) > 4096:
            raise ValueError("realtime payload is too large")
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO realtime_events (
                    event_id, user_uid, event_type, aggregate_type,
                    aggregate_id, payload, created_at, expires_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    new_id("rtevt"),
                    tenant.user_uid,
                    normalized_type,
                    str(aggregate_type or "")[:64],
                    str(aggregate_id).strip() if aggregate_id else None,
                    encoded,
                    float(now),
                    float(expires_at),
                ),
            )
            return int(cursor.lastrowid)

    async def fetch_after(
        self,
        tenant: TenantContext,
        *,
        after: int,
        now: float,
        limit: int = 100,
    ) -> tuple[list[RealtimeEvent], int, bool]:
        cursor_value = max(int(after), 0)
        async with self.connection.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                """
                SELECT COALESCE(MAX(sequence_id), 0) AS expired_sequence
                FROM realtime_events
                WHERE user_uid = %s AND expires_at <= %s
                """,
                (tenant.user_uid, float(now)),
            )
            expired_sequence = int((await cursor.fetchone())["expired_sequence"] or 0)
            resync_required = cursor_value > 0 and expired_sequence > cursor_value
            await cursor.execute(
                """
                SELECT sequence_id, event_type, aggregate_id, payload, created_at
                FROM realtime_events
                WHERE user_uid = %s AND sequence_id > %s AND expires_at > %s
                ORDER BY sequence_id ASC
                LIMIT %s
                """,
                (tenant.user_uid, cursor_value, float(now), min(max(int(limit), 1), 500)),
            )
            rows = [dict(row) for row in await cursor.fetchall()]
            await cursor.execute(
                "SELECT COALESCE(MAX(sequence_id), 0) AS current_sequence FROM realtime_events WHERE user_uid = %s",
                (tenant.user_uid,),
            )
            current = int((await cursor.fetchone())["current_sequence"] or 0)
        events = []
        for row in rows:
            payload = row["payload"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            events.append(
                RealtimeEvent(
                    sequence=int(row["sequence_id"]),
                    event_type=str(row["event_type"]),
                    aggregate_id=str(row["aggregate_id"]) if row["aggregate_id"] else None,
                    occurred_at=float(row["created_at"] or 0),
                    payload=dict(payload or {}),
                )
            )
        return events, current, resync_required

    async def cleanup(self, *, now: float, per_user_limit: int = 10000) -> int:
        maximum = max(int(per_user_limit), 1)
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                "DELETE FROM realtime_events WHERE expires_at <= %s",
                (float(now),),
            )
            deleted = int(cursor.rowcount)
            await cursor.execute("SELECT DISTINCT user_uid FROM realtime_events")
            users = [str(row[0]) for row in await cursor.fetchall()]
            for user_uid in users:
                await cursor.execute(
                    """
                    DELETE FROM realtime_events
                    WHERE user_uid = %s
                      AND sequence_id NOT IN (
                          SELECT sequence_id FROM (
                              SELECT sequence_id
                              FROM realtime_events
                              WHERE user_uid = %s
                              ORDER BY sequence_id DESC
                              LIMIT %s
                          ) retained
                      )
                    """,
                    (user_uid, user_uid, maximum),
                )
                deleted += int(cursor.rowcount)
        return deleted
