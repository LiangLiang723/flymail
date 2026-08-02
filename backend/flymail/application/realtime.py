"""Persisted realtime backlog and WebSocket delivery orchestration."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from flymail.application.auth import AuthService, AuthenticatedSession, SESSION_COOKIE_NAME
from flymail.domain.errors import ApiContractError, AuthenticationError
from flymail.infrastructure.db.pool import DatabasePool
from flymail.repositories.base import TenantContext
from flymail.repositories.realtime import RealtimeEvent, RealtimeRepository


class RealtimeService:
    def __init__(
        self,
        pool: DatabasePool,
        *,
        auth_service: AuthService | Any,
        allowed_origin: str | None = None,
        wait_timeout: float = 15.0,
        send_timeout: float = 5.0,
        now_fn=time.time,
    ) -> None:
        if not isinstance(pool, DatabasePool):
            raise TypeError("pool must be DatabasePool")
        self.pool = pool
        self.auth_service = auth_service
        self.allowed_origin = str(allowed_origin or "").rstrip("/")
        self.wait_timeout = max(float(wait_timeout), 0.001)
        self.send_timeout = max(float(send_timeout), 0.001)
        self.now_fn = now_fn
        self._condition = asyncio.Condition()

    @staticmethod
    def _event(value: RealtimeEvent) -> dict[str, Any]:
        return {
            "sequence": value.sequence,
            "event_type": value.event_type,
            "aggregate_id": value.aggregate_id,
            "occurred_at": value.occurred_at,
            "payload": value.payload,
        }

    async def fetch(
        self,
        session: AuthenticatedSession,
        *,
        after: int,
        limit: int = 100,
    ) -> dict[str, Any]:
        tenant = TenantContext(session.user.id)
        async with self.pool.acquire() as connection:
            events, current, resync = await RealtimeRepository(connection).fetch_after(
                tenant,
                after=after,
                now=float(self.now_fn()),
                limit=limit,
            )
        if resync:
            raise ApiContractError(
                "resync_required",
                "实时事件保留窗口已过期，需要重新同步",
                status_code=409,
                details={
                    "scopes": ["bootstrap", "threads", "accounts", "settings"],
                    "current_sequence": current,
                },
            )
        return {
            "events": [self._event(item) for item in events],
            "current_sequence": current,
        }

    async def publish(
        self,
        tenant: TenantContext,
        *,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str | None,
        payload: dict[str, object],
        ttl_seconds: float = 7 * 24 * 3600,
    ) -> int:
        now = float(self.now_fn())
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                sequence = await RealtimeRepository(connection).append(
                    tenant,
                    event_type=event_type,
                    aggregate_type=aggregate_type,
                    aggregate_id=aggregate_id,
                    payload=payload,
                    now=now,
                    expires_at=now + max(float(ttl_seconds), 60),
                )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
        async with self._condition:
            self._condition.notify_all()
        return sequence

    def _expected_origin(self, websocket) -> str:
        if self.allowed_origin:
            return self.allowed_origin
        scheme = str(getattr(getattr(websocket, "url", None), "scheme", "https"))
        host = str(websocket.headers.get("host", ""))
        return f"{scheme}://{host}".rstrip("/")

    async def _send(self, websocket, payload: dict[str, Any]) -> bool:
        try:
            await asyncio.wait_for(
                websocket.send_json(payload),
                timeout=self.send_timeout,
            )
            return True
        except (TimeoutError, asyncio.TimeoutError):
            await websocket.close(code=1013, reason="client too slow")
            return False

    async def serve_websocket(self, websocket) -> None:
        origin = str(websocket.headers.get("origin", "")).rstrip("/")
        if not origin or origin != self._expected_origin(websocket):
            await websocket.close(code=4403, reason="origin not allowed")
            return
        cookie = str(websocket.cookies.get(SESSION_COOKIE_NAME, ""))
        if not cookie:
            await websocket.close(code=4401, reason="authentication required")
            return
        try:
            after = max(int(str(websocket.query_params.get("after", "0"))), 0)
        except ValueError:
            await websocket.close(code=4400, reason="invalid cursor")
            return
        try:
            session = await self.auth_service.authenticate(cookie)
        except AuthenticationError:
            await websocket.close(code=4401, reason="authentication failed")
            return
        await websocket.accept()
        cursor = after
        while True:
            try:
                payload = await self.fetch(session, after=cursor)
            except ApiContractError as exc:
                if exc.code == "resync_required":
                    await self._send(
                        websocket,
                        {
                            "type": "resync_required",
                            "details": exc.details or {},
                        },
                    )
                    await websocket.close(code=4409, reason="resync required")
                    return
                raise
            events = payload["events"]
            if events:
                if not await self._send(
                    websocket,
                    {
                        "type": "events",
                        "events": events,
                        "current_sequence": payload["current_sequence"],
                    },
                ):
                    return
                cursor = int(events[-1]["sequence"])
            try:
                session = await self.auth_service.authenticate(cookie)
            except AuthenticationError:
                await websocket.close(code=4401, reason="session revoked")
                return
            try:
                async with self._condition:
                    await asyncio.wait_for(
                        self._condition.wait(),
                        timeout=self.wait_timeout,
                    )
            except (TimeoutError, asyncio.TimeoutError):
                if not await self._send(
                    websocket,
                    {"type": "ping", "current_sequence": payload["current_sequence"]},
                ):
                    return
