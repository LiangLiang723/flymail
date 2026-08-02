"""Administrator-authorized storage roots and current-user safe browsing routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, status

from flymail.api.dependencies import require_admin, require_csrf, require_session
from flymail.api.schemas.personal import (
    StorageBrowseResponse,
    StorageRootCreateRequest,
    StorageRootListResponse,
    StorageRootResponse,
)
from flymail.application.auth import AuthenticatedSession
from flymail.application.storage_paths import StoragePathService
from flymail.repositories.users import User


router = APIRouter(tags=["storage"])


def _service(request: Request) -> StoragePathService:
    service = getattr(request.app.state, "storage_path_service", None)
    if not isinstance(service, StoragePathService):
        raise RuntimeError("storage path service is unavailable")
    return service


@router.post(
    "/api/v2/admin/storage-roots",
    response_model=StorageRootResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_storage_root(
    payload: StorageRootCreateRequest,
    request: Request,
    _session: AuthenticatedSession = Depends(require_csrf),
    admin: User = Depends(require_admin),
) -> StorageRootResponse:
    return await _service(request).create_root(
        admin,
        label=payload.label,
        path=payload.path,
        visibility_scope=payload.visibility_scope,
        user_uid=payload.user_uid,
        request_id=request.state.context.request_id,
    )


@router.get("/api/v2/storage/roots", response_model=StorageRootListResponse)
async def list_storage_roots(
    request: Request,
    session: AuthenticatedSession = Depends(require_session),
) -> StorageRootListResponse:
    return StorageRootListResponse(items=await _service(request).list_roots(session))


@router.get(
    "/api/v2/storage/roots/{root_id}/browse",
    response_model=StorageBrowseResponse,
)
async def browse_storage_root(
    root_id: str,
    request: Request,
    path: str = Query(default="", max_length=1024),
    session: AuthenticatedSession = Depends(require_session),
) -> StorageBrowseResponse:
    return await _service(request).browse(session, root_id, path)
