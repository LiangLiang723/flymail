"""Administrator history-sync inspection and control routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from flymail.api.dependencies import require_csrf, require_session
from flymail.api.schemas.settings_contacts import (
    HistorySyncActionResponse,
    HistorySyncListResponse,
)
from flymail.application.auth import AuthenticatedSession
from flymail.application.settings_contacts import AdminHistorySyncService
from flymail.domain.errors import AuthorizationError


router = APIRouter(tags=["admin-history-sync"])


def _service(request: Request) -> AdminHistorySyncService:
    service = getattr(request.app.state, "admin_history_sync_service", None)
    if not isinstance(service, AdminHistorySyncService):
        raise RuntimeError("admin history sync service is unavailable")
    return service


def _assert_admin(session: AuthenticatedSession) -> None:
    if session.user.role != "admin":
        raise AuthorizationError("administrator role is required")


@router.get("/api/v2/admin/history-sync", response_model=HistorySyncListResponse)
async def list_history_sync(
    request: Request,
    session: AuthenticatedSession = Depends(require_session),
) -> HistorySyncListResponse:
    _assert_admin(session)
    return HistorySyncListResponse(items=await _service(request).list_jobs())


async def _action(
    action: str,
    job_id: str,
    request: Request,
    session: AuthenticatedSession,
) -> HistorySyncActionResponse:
    _assert_admin(session)
    service = _service(request)
    handler = getattr(service, action)
    result = await handler(
        session,
        job_id,
        request.state.context.request_id,
    )
    return HistorySyncActionResponse(job_id=result.job_id, status=result.status)


@router.post(
    "/api/v2/admin/history-sync/{job_id}/pause",
    response_model=HistorySyncActionResponse,
)
async def pause_history_sync(
    job_id: str,
    request: Request,
    session: AuthenticatedSession = Depends(require_csrf),
) -> HistorySyncActionResponse:
    return await _action("pause", job_id, request, session)


@router.post(
    "/api/v2/admin/history-sync/{job_id}/resume",
    response_model=HistorySyncActionResponse,
)
async def resume_history_sync(
    job_id: str,
    request: Request,
    session: AuthenticatedSession = Depends(require_csrf),
) -> HistorySyncActionResponse:
    return await _action("resume", job_id, request, session)


@router.post(
    "/api/v2/admin/history-sync/{job_id}/retry",
    response_model=HistorySyncActionResponse,
)
async def retry_history_sync(
    job_id: str,
    request: Request,
    session: AuthenticatedSession = Depends(require_csrf),
) -> HistorySyncActionResponse:
    return await _action("retry", job_id, request, session)
