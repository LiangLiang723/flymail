"""Tenant-scoped notification-center queries and safe preferences."""

from __future__ import annotations

import json
import time
from typing import Any

import aiomysql

from flymail.api.schemas.notifications import (
    NotificationItem,
    NotificationListResponse,
    NotificationSettingsRequest,
    NotificationSettingsResponse,
    QuietHours,
)
from flymail.application.auth import AuthenticatedSession
from flymail.application.realtime import RealtimeService
from flymail.application.thread_queries import ThreadCursorCodec
from flymail.domain.errors import NotFoundError
from flymail.infrastructure.db.pool import DatabasePool
from flymail.repositories.base import TenantContext


class NotificationApiService:
    def __init__(
        self,
        pool: DatabasePool,
        realtime: RealtimeService,
        cursor_secret: str,
        *,
        now_fn=time.time,
    ) -> None:
        if not isinstance(pool, DatabasePool):
            raise TypeError("pool must be DatabasePool")
        if not isinstance(realtime, RealtimeService):
            raise TypeError("realtime must be RealtimeService")
        self.pool = pool
        self.realtime = realtime
        self.cursor = ThreadCursorCodec(cursor_secret)
        self.now_fn = now_fn

    @staticmethod
    def _json_object(value: object) -> dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        if value in (None, ""):
            return {}
        try:
            decoded = json.loads(str(value))
        except (TypeError, json.JSONDecodeError):
            return {}
        return dict(decoded) if isinstance(decoded, dict) else {}

    async def list_notifications(
        self,
        session: AuthenticatedSession,
        *,
        limit: int,
        cursor: str | None,
    ) -> NotificationListResponse:
        tenant = TenantContext(session.user.id)
        position = self.cursor.decode(cursor)
        page_size = min(max(int(limit), 1), 100)
        conditions = ["user_uid = %s", "dismissed_at IS NULL"]
        params: list[object] = [tenant.user_uid]
        if position is not None:
            conditions.append("(created_at < %s OR (created_at = %s AND id < %s))")
            params.extend((position[0], position[0], position[1]))
        params.append(page_size + 1)
        async with self.pool.acquire() as connection:
            async with connection.cursor(aiomysql.DictCursor) as sql:
                await sql.execute(
                    f"""
                    SELECT id, event_type, title, summary, action_path,
                           account_id, created_at, read_at
                    FROM notification_events
                    WHERE {' AND '.join(conditions)}
                    ORDER BY created_at DESC, id DESC
                    LIMIT %s
                    """,
                    tuple(params),
                )
                rows = [dict(row) for row in await sql.fetchall()]
                await sql.execute(
                    """
                    SELECT COUNT(*) AS unread_count
                    FROM notification_events
                    WHERE user_uid = %s AND read_at IS NULL
                      AND dismissed_at IS NULL
                    """,
                    (tenant.user_uid,),
                )
                unread_count = int((await sql.fetchone())["unread_count"] or 0)
        visible = rows[:page_size]
        items = tuple(
            NotificationItem(
                id=str(row["id"]),
                event_type=str(row["event_type"]),
                title=str(row["title"] or ""),
                summary=str(row["summary"] or ""),
                action_path=str(row["action_path"] or ""),
                account_id=str(row["account_id"]) if row["account_id"] else None,
                created_at=float(row["created_at"] or 0),
                read=row["read_at"] is not None,
            )
            for row in visible
        )
        next_cursor = None
        if len(rows) > page_size and items:
            last = items[-1]
            next_cursor = self.cursor.encode(last.created_at, last.id)
        return NotificationListResponse(
            items=items,
            next_cursor=next_cursor,
            unread_count=max(unread_count, 0),
        )

    async def mark_read(
        self,
        session: AuthenticatedSession,
        notification_id: str,
    ) -> None:
        tenant = TenantContext(session.user.id)
        normalized_id = str(notification_id or "").strip()
        timestamp = float(self.now_fn())
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        UPDATE notification_events
                        SET read_at = COALESCE(read_at, %s)
                        WHERE id = %s AND user_uid = %s
                          AND dismissed_at IS NULL
                        """,
                        (timestamp, normalized_id, tenant.user_uid),
                    )
                    changed = cursor.rowcount == 1
                    if not changed:
                        await cursor.execute(
                            """
                            SELECT id FROM notification_events
                            WHERE id = %s AND user_uid = %s
                              AND dismissed_at IS NULL
                            """,
                            (normalized_id, tenant.user_uid),
                        )
                        if await cursor.fetchone() is None:
                            raise NotFoundError("notification was not found")
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
        await self._publish(tenant, normalized_id, "read")

    async def dismiss(
        self,
        session: AuthenticatedSession,
        notification_id: str,
    ) -> None:
        tenant = TenantContext(session.user.id)
        normalized_id = str(notification_id or "").strip()
        timestamp = float(self.now_fn())
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        UPDATE notification_events
                        SET dismissed_at = COALESCE(dismissed_at, %s),
                            read_at = COALESCE(read_at, %s)
                        WHERE id = %s AND user_uid = %s
                        """,
                        (timestamp, timestamp, normalized_id, tenant.user_uid),
                    )
                    if cursor.rowcount != 1:
                        raise NotFoundError("notification was not found")
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
        await self._publish(tenant, normalized_id, "dismissed")

    async def mark_all_read(self, session: AuthenticatedSession) -> int:
        tenant = TenantContext(session.user.id)
        timestamp = float(self.now_fn())
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        UPDATE notification_events
                        SET read_at = %s
                        WHERE user_uid = %s AND read_at IS NULL
                          AND dismissed_at IS NULL
                        """,
                        (timestamp, tenant.user_uid),
                    )
                    changed = int(cursor.rowcount)
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
        await self._publish(tenant, None, "all_read")
        return changed

    async def _publish(
        self,
        tenant: TenantContext,
        notification_id: str | None,
        state: str,
    ) -> None:
        await self.realtime.publish(
            tenant,
            event_type="notification.updated",
            aggregate_type="notification",
            aggregate_id=notification_id,
            payload={"notification_id": notification_id, "state": state},
        )

    async def get_settings(
        self,
        session: AuthenticatedSession,
    ) -> NotificationSettingsResponse:
        tenant = TenantContext(session.user.id)
        async with self.pool.acquire() as connection:
            async with connection.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(
                    """
                    SELECT in_app_enabled, external_enabled, include_images,
                           quiet_hours_json, event_preferences_json, updated_at
                    FROM notification_preferences
                    WHERE user_uid = %s
                    """,
                    (tenant.user_uid,),
                )
                row = await cursor.fetchone()
        if row is None:
            return NotificationSettingsResponse(
                in_app_enabled=True,
                external_enabled=True,
                include_images=False,
                quiet_hours=None,
                event_preferences={},
                updated_at=0,
            )
        quiet = self._json_object(row["quiet_hours_json"])
        return NotificationSettingsResponse(
            in_app_enabled=bool(row["in_app_enabled"]),
            external_enabled=bool(row["external_enabled"]),
            include_images=bool(row["include_images"]),
            quiet_hours=QuietHours.model_validate(quiet) if quiet else None,
            event_preferences={
                str(key): bool(value)
                for key, value in self._json_object(
                    row["event_preferences_json"]
                ).items()
            },
            updated_at=float(row["updated_at"] or 0),
        )

    async def update_settings(
        self,
        session: AuthenticatedSession,
        payload: NotificationSettingsRequest,
    ) -> NotificationSettingsResponse:
        tenant = TenantContext(session.user.id)
        timestamp = float(self.now_fn())
        quiet = payload.quiet_hours.model_dump(mode="json") if payload.quiet_hours else None
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        INSERT INTO notification_preferences (
                            user_uid, in_app_enabled, external_enabled,
                            include_images, quiet_hours_json,
                            event_preferences_json, created_at, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        AS incoming
                        ON DUPLICATE KEY UPDATE
                            in_app_enabled = incoming.in_app_enabled,
                            external_enabled = incoming.external_enabled,
                            include_images = incoming.include_images,
                            quiet_hours_json = incoming.quiet_hours_json,
                            event_preferences_json = incoming.event_preferences_json,
                            updated_at = incoming.updated_at
                        """,
                        (
                            tenant.user_uid,
                            1 if payload.in_app_enabled else 0,
                            1 if payload.external_enabled else 0,
                            1 if payload.include_images else 0,
                            json.dumps(quiet, ensure_ascii=False) if quiet else None,
                            json.dumps(
                                payload.event_preferences,
                                ensure_ascii=False,
                                sort_keys=True,
                            ),
                            timestamp,
                            timestamp,
                        ),
                    )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
        await self.realtime.publish(
            tenant,
            event_type="settings.updated",
            aggregate_type="notification_preferences",
            aggregate_id=tenant.user_uid,
            payload={"settings_scope": "notifications"},
        )
        return await self.get_settings(session)
