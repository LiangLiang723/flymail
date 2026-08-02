"""Current-user sync center, conflict actions, and administrator diagnostics."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status

from flymail.api.dependencies import require_admin, require_csrf, require_session
from flymail.api.schemas.sync import (
    AdminDiagnosticsResponse,
    ConflictListResponse,
    ConflictResolutionRequest,
    ConflictResolutionResponse,
    SyncCenterResponse,
    SyncTaskResponse,
)
from flymail.application.auth import AuthenticatedSession
from flymail.application.sync_status import SyncStatusService
from flymail.repositories.users import User


router = APIRouter(tags=["sync"])


def _service(request: Request) -> SyncStatusService:
    service = getattr(request.app.state, "sync_status_service", None)
    if not isinstance(service, SyncStatusService):
        raise RuntimeError("sync status service is unavailable")
    return service


@router.get("/api/v2/sync", response_model=SyncCenterResponse)
async def sync_center(
    request: Request,
    session: AuthenticatedSession = Depends(require_session),
) -> SyncCenterResponse:
    return await _service(request).center(session)


@router.post(
    "/api/v2/sync/accounts/{account_id}/refresh",
    response_model=SyncTaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def refresh_account(
    account_id: str,
    request: Request,
    session: AuthenticatedSession = Depends(require_csrf),
) -> SyncTaskResponse:
    return await _service(request).request_refresh(
        session,
        account_id,
        request_id=request.state.context.request_id,
    )


@router.get("/api/v2/sync/conflicts", response_model=ConflictListResponse)
async def list_conflicts(
    request: Request,
    session: AuthenticatedSession = Depends(require_session),
) -> ConflictListResponse:
    return ConflictListResponse(items=await _service(request).list_conflicts(session))


@router.post(
    "/api/v2/sync/conflicts/{operation_id}/resolve",
    response_model=ConflictResolutionResponse,
)
async def resolve_conflict(
    operation_id: str,
    payload: ConflictResolutionRequest,
    request: Request,
    session: AuthenticatedSession = Depends(require_csrf),
) -> ConflictResolutionResponse:
    return await _service(request).resolve_conflict(
        session,
        operation_id,
        action=payload.action,
        mailbox_id=payload.mailbox_id,
        request_id=request.state.context.request_id,
    )


@router.get("/api/v2/admin/diagnostics", response_model=AdminDiagnosticsResponse)
async def admin_diagnostics(
    request: Request,
    _admin: User = Depends(require_admin),
) -> AdminDiagnosticsResponse:
    return await _service(request).diagnostics()
