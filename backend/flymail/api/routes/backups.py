"""Administrator backup archive and isolated restore-rehearsal routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import FileResponse

from flymail.api.dependencies import require_csrf, require_session
from flymail.api.schemas.backups import (
    BackupArchiveListResponse,
    BackupArchiveResponse,
    BackupInspectionResponse,
    BackupPasswordRequest,
    RestoreRehearsalResponse,
)
from flymail.application.auth import AuthenticatedSession
from flymail.application.backups import BackupService
from flymail.domain.errors import AuthorizationError


router = APIRouter(tags=["admin-backups"])


def _service(request: Request) -> BackupService:
    service = getattr(request.app.state, "backup_service", None)
    if not isinstance(service, BackupService):
        raise RuntimeError("backup service is unavailable")
    return service


def _assert_admin(session: AuthenticatedSession) -> None:
    if session.user.role != "admin":
        raise AuthorizationError("administrator role is required")


@router.post(
    "/api/v2/admin/backups",
    response_model=BackupArchiveResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_backup(
    payload: BackupPasswordRequest,
    request: Request,
    session: AuthenticatedSession = Depends(require_csrf),
) -> BackupArchiveResponse:
    _assert_admin(session)
    return await _service(request).create_archive(
        session,
        password=payload.password,
        request_id=request.state.context.request_id,
    )


@router.get(
    "/api/v2/admin/backups",
    response_model=BackupArchiveListResponse,
)
async def list_backups(
    request: Request,
    session: AuthenticatedSession = Depends(require_session),
) -> BackupArchiveListResponse:
    _assert_admin(session)
    return BackupArchiveListResponse(items=await _service(request).list_archives())


@router.get(
    "/api/v2/admin/backups/{backup_id}",
    response_model=BackupArchiveResponse,
)
async def get_backup(
    backup_id: str,
    request: Request,
    session: AuthenticatedSession = Depends(require_session),
) -> BackupArchiveResponse:
    _assert_admin(session)
    return await _service(request).get_archive(backup_id)


@router.get("/api/v2/admin/backups/{backup_id}/download")
async def download_backup(
    backup_id: str,
    request: Request,
    session: AuthenticatedSession = Depends(require_session),
) -> FileResponse:
    _assert_admin(session)
    backup = await _service(request).download(backup_id)
    return FileResponse(
        backup.path,
        media_type=backup.content_type,
        filename=backup.filename,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.post(
    "/api/v2/admin/backups/{backup_id}/inspect",
    response_model=BackupInspectionResponse,
)
async def inspect_backup(
    backup_id: str,
    payload: BackupPasswordRequest,
    request: Request,
    session: AuthenticatedSession = Depends(require_csrf),
) -> BackupInspectionResponse:
    _assert_admin(session)
    return await _service(request).inspect(backup_id, password=payload.password)


@router.post(
    "/api/v2/admin/backups/{backup_id}/restore-rehearsal",
    response_model=RestoreRehearsalResponse,
)
async def restore_rehearsal(
    backup_id: str,
    payload: BackupPasswordRequest,
    request: Request,
    session: AuthenticatedSession = Depends(require_csrf),
) -> RestoreRehearsalResponse:
    _assert_admin(session)
    return await _service(request).restore_rehearsal(
        session,
        backup_id,
        password=payload.password,
        request_id=request.state.context.request_id,
    )
