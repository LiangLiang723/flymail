"""Persisted realtime backlog and WebSocket routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, WebSocket
from fastapi.responses import JSONResponse

from flymail.api.dependencies import require_session
from flymail.application.auth import AuthenticatedSession
from flymail.application.realtime import RealtimeService


router = APIRouter(tags=["realtime"])


def _service(application) -> RealtimeService:
    service = getattr(application.state, "realtime_service", None)
    if not isinstance(service, RealtimeService):
        raise RuntimeError("realtime service is unavailable")
    return service


@router.get("/api/v2/events")
async def list_events(
    request: Request,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AuthenticatedSession = Depends(require_session),
) -> JSONResponse:
    payload = await _service(request.app).fetch(session, after=after, limit=limit)
    return JSONResponse(
        content=payload,
        headers={"Cache-Control": "no-store"},
    )


@router.websocket("/api/v2/realtime")
@router.websocket("/api/v2/ws")
async def realtime_websocket(websocket: WebSocket) -> None:
    await _service(websocket.app).serve_websocket(websocket)
