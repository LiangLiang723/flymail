"""Notification center and safe preference routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import JSONResponse

from flymail.api.dependencies import require_csrf, require_session
from flymail.api.schemas.notifications import (
    NotificationListResponse,
    NotificationReadAllResponse,
    NotificationReadResponse,
    NotificationSettingsRequest,
    NotificationSettingsResponse,
)
from flymail.application.auth import AuthenticatedSession
from flymail.application.notifications_api import NotificationApiService


router = APIRouter(tags=["notifications"])


def _service(request: Request) -> NotificationApiService:
    service = getattr(request.app.state, "notification_api_service", None)
    if not isinstance(service, NotificationApiService):
        raise RuntimeError("notification API service is unavailable")
    return service


@router.get("/api/v2/notifications", response_model=NotificationListResponse)
async def list_notifications(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=2048),
    session: AuthenticatedSession = Depends(require_session),
) -> JSONResponse:
    payload = await _service(request).list_notifications(
        session,
        limit=limit,
        cursor=cursor,
    )
    return JSONResponse(
        content=payload.model_dump(mode="json"),
        headers={"Cache-Control": "no-store"},
    )


@router.post(
    "/api/v2/notifications/read-all",
    response_model=NotificationReadAllResponse,
)
async def read_all_notifications(
    request: Request,
    session: AuthenticatedSession = Depends(require_csrf),
) -> NotificationReadAllResponse:
    return NotificationReadAllResponse(
        updated_count=await _service(request).mark_all_read(session)
    )


@router.post(
    "/api/v2/notifications/{notification_id}/read",
    response_model=NotificationReadResponse,
)
async def read_notification(
    notification_id: str,
    request: Request,
    session: AuthenticatedSession = Depends(require_csrf),
) -> NotificationReadResponse:
    await _service(request).mark_read(session, notification_id)
    return NotificationReadResponse(id=notification_id, read=True)


@router.post(
    "/api/v2/notifications/{notification_id}/dismiss",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def dismiss_notification(
    notification_id: str,
    request: Request,
    session: AuthenticatedSession = Depends(require_csrf),
) -> Response:
    await _service(request).dismiss(session, notification_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/api/v2/notification-settings",
    response_model=NotificationSettingsResponse,
)
async def get_notification_settings(
    request: Request,
    session: AuthenticatedSession = Depends(require_session),
) -> NotificationSettingsResponse:
    return await _service(request).get_settings(session)


@router.put(
    "/api/v2/notification-settings",
    response_model=NotificationSettingsResponse,
)
async def update_notification_settings(
    payload: NotificationSettingsRequest,
    request: Request,
    session: AuthenticatedSession = Depends(require_csrf),
) -> NotificationSettingsResponse:
    return await _service(request).update_settings(session, payload)
