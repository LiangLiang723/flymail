"""Notification center and safe preference routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import JSONResponse

from flymail.api.dependencies import require_csrf, require_session
from flymail.api.schemas.notifications import (
    NotificationChannelListResponse,
    NotificationChannelRequest,
    NotificationChannelResponse,
    NotificationListResponse,
    NotificationPublisherRequest,
    NotificationPublisherResponse,
    NotificationReadAllResponse,
    NotificationReadResponse,
    NotificationRuleRequest,
    NotificationRuleResponse,
    NotificationSettingsRequest,
    NotificationSettingsResponse,
    NotificationTestResponse,
)
from flymail.application.auth import AuthenticatedSession
from flymail.application.notification_config import NotificationConfigService
from flymail.application.notifications_api import NotificationApiService


router = APIRouter(tags=["notifications"])


def _service(request: Request) -> NotificationApiService:
    service = getattr(request.app.state, "notification_api_service", None)
    if not isinstance(service, NotificationApiService):
        raise RuntimeError("notification API service is unavailable")
    return service


def _config_service(request: Request) -> NotificationConfigService:
    service = getattr(request.app.state, "notification_config_service", None)
    if not isinstance(service, NotificationConfigService):
        raise RuntimeError("notification configuration service is unavailable")
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


@router.get(
    "/api/v2/notification-channels",
    response_model=NotificationChannelListResponse,
)
async def list_notification_channels(
    request: Request,
    session: AuthenticatedSession = Depends(require_session),
) -> NotificationChannelListResponse:
    return NotificationChannelListResponse(
        items=await _config_service(request).list_channels(session)
    )


@router.post(
    "/api/v2/notification-channels",
    response_model=NotificationChannelResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_notification_channel(
    payload: NotificationChannelRequest,
    request: Request,
    session: AuthenticatedSession = Depends(require_csrf),
) -> NotificationChannelResponse:
    return await _config_service(request).create_channel(
        session,
        channel_key=payload.channel_key,
        display_name=payload.display_name,
        enabled=payload.enabled,
        public_config=payload.public_config,
        secret=payload.secret,
        use_proxy=payload.use_proxy,
        request_id=request.state.context.request_id,
    )


@router.put(
    "/api/v2/notification-channels/{channel_id}",
    response_model=NotificationChannelResponse,
)
async def update_notification_channel(
    channel_id: str,
    payload: NotificationChannelRequest,
    request: Request,
    session: AuthenticatedSession = Depends(require_csrf),
) -> NotificationChannelResponse:
    return await _config_service(request).update_channel(
        session,
        channel_id,
        channel_key=payload.channel_key,
        display_name=payload.display_name,
        enabled=payload.enabled,
        public_config=payload.public_config,
        secret=payload.secret,
        use_proxy=payload.use_proxy,
        request_id=request.state.context.request_id,
    )


@router.delete(
    "/api/v2/notification-channels/{channel_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_notification_channel(
    channel_id: str,
    request: Request,
    session: AuthenticatedSession = Depends(require_csrf),
) -> Response:
    await _config_service(request).delete_channel(
        session,
        channel_id,
        request_id=request.state.context.request_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/api/v2/notification-channels/{channel_id}/test",
    response_model=NotificationTestResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def test_notification_channel(
    channel_id: str,
    request: Request,
    session: AuthenticatedSession = Depends(require_csrf),
) -> NotificationTestResponse:
    return await _config_service(request).test_channel(
        session,
        channel_id,
        request_id=request.state.context.request_id,
    )


@router.get(
    "/api/v2/notification-rules",
    response_model=list[NotificationRuleResponse],
)
async def list_notification_rules(
    request: Request,
    session: AuthenticatedSession = Depends(require_session),
) -> tuple[NotificationRuleResponse, ...]:
    return await _config_service(request).list_rules(session)


@router.post(
    "/api/v2/notification-rules",
    response_model=NotificationRuleResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_notification_rule(
    payload: NotificationRuleRequest,
    request: Request,
    session: AuthenticatedSession = Depends(require_csrf),
) -> NotificationRuleResponse:
    return await _config_service(request).create_rule(
        session,
        event_type=payload.event_type,
        channel_id=payload.channel_id,
        image_publisher_id=payload.image_publisher_id,
        enabled=payload.enabled,
        use_proxy=payload.use_proxy,
        dedupe_window_seconds=payload.dedupe_window_seconds,
        request_id=request.state.context.request_id,
    )


@router.put(
    "/api/v2/notification-rules/{rule_id}",
    response_model=NotificationRuleResponse,
)
async def update_notification_rule(
    rule_id: str,
    payload: NotificationRuleRequest,
    request: Request,
    session: AuthenticatedSession = Depends(require_csrf),
) -> NotificationRuleResponse:
    return await _config_service(request).update_rule(
        session,
        rule_id,
        event_type=payload.event_type,
        channel_id=payload.channel_id,
        image_publisher_id=payload.image_publisher_id,
        enabled=payload.enabled,
        use_proxy=payload.use_proxy,
        dedupe_window_seconds=payload.dedupe_window_seconds,
        request_id=request.state.context.request_id,
    )


@router.delete(
    "/api/v2/notification-rules/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_notification_rule(
    rule_id: str,
    request: Request,
    session: AuthenticatedSession = Depends(require_csrf),
) -> Response:
    await _config_service(request).delete_rule(
        session,
        rule_id,
        request_id=request.state.context.request_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/api/v2/notification-publishers",
    response_model=list[NotificationPublisherResponse],
)
async def list_notification_publishers(
    request: Request,
    session: AuthenticatedSession = Depends(require_session),
) -> tuple[NotificationPublisherResponse, ...]:
    return await _config_service(request).list_publishers(session)


@router.post(
    "/api/v2/notification-publishers",
    response_model=NotificationPublisherResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_notification_publisher(
    payload: NotificationPublisherRequest,
    request: Request,
    session: AuthenticatedSession = Depends(require_csrf),
) -> NotificationPublisherResponse:
    return await _config_service(request).create_publisher(
        session,
        publisher_key=payload.publisher_key,
        display_name=payload.display_name,
        endpoint_url=payload.endpoint_url,
        enabled=payload.enabled,
        public_config=payload.public_config,
        secret=payload.secret,
        request_id=request.state.context.request_id,
    )


@router.put(
    "/api/v2/notification-publishers/{publisher_id}",
    response_model=NotificationPublisherResponse,
)
async def update_notification_publisher(
    publisher_id: str,
    payload: NotificationPublisherRequest,
    request: Request,
    session: AuthenticatedSession = Depends(require_csrf),
) -> NotificationPublisherResponse:
    return await _config_service(request).update_publisher(
        session,
        publisher_id,
        publisher_key=payload.publisher_key,
        display_name=payload.display_name,
        endpoint_url=payload.endpoint_url,
        enabled=payload.enabled,
        public_config=payload.public_config,
        secret=payload.secret,
        request_id=request.state.context.request_id,
    )


@router.delete(
    "/api/v2/notification-publishers/{publisher_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_notification_publisher(
    publisher_id: str,
    request: Request,
    session: AuthenticatedSession = Depends(require_csrf),
) -> Response:
    await _config_service(request).delete_publisher(
        session,
        publisher_id,
        request_id=request.state.context.request_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
