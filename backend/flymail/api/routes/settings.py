"""Current-user settings routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from flymail.api.dependencies import require_csrf, require_session
from flymail.api.schemas.settings_contacts import SettingsResponse, SettingsUpdateRequest
from flymail.application.auth import AuthenticatedSession
from flymail.application.settings_contacts import SettingsContactsService


router = APIRouter(tags=["settings"])


def _service(request: Request) -> SettingsContactsService:
    service = getattr(request.app.state, "settings_contacts_service", None)
    if not isinstance(service, SettingsContactsService):
        raise RuntimeError("settings service is unavailable")
    return service


@router.get("/api/v2/settings", response_model=SettingsResponse)
async def get_settings(
    request: Request,
    session: AuthenticatedSession = Depends(require_session),
) -> SettingsResponse:
    return await _service(request).get_settings(session)


@router.put("/api/v2/settings", response_model=SettingsResponse)
async def update_settings(
    payload: SettingsUpdateRequest,
    request: Request,
    session: AuthenticatedSession = Depends(require_csrf),
) -> SettingsResponse:
    return await _service(request).update_settings(
        session,
        body_cache_quota_bytes=payload.body_cache_quota_bytes,
        attachment_cache_quota_bytes=payload.attachment_cache_quota_bytes,
        ui_preferences=payload.ui_preferences,
        compose_preferences=payload.compose_preferences,
        remote_image_policy=payload.remote_image_policy,
        request_id=request.state.context.request_id,
    )
