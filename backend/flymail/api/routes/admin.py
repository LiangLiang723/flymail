"""Administrator user and session management routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status

from flymail.api.dependencies import (
    get_auth_service,
    get_request_context,
    require_admin,
    require_csrf,
)
from flymail.api.schemas.auth import (
    CreateUserRequest,
    ResetPasswordRequest,
    RevokeSessionsRequest,
    RevokeSessionsResponse,
    UserListResponse,
    UserResponse,
)
from flymail.application.auth import AuthService, AuthenticatedSession
from flymail.repositories.users import User


router = APIRouter(prefix="/api/v2/admin", tags=["administration"])


@router.get("/users", response_model=UserListResponse)
async def list_users(
    admin_user: User = Depends(require_admin),
    service: AuthService = Depends(get_auth_service),
) -> UserListResponse:
    users = await service.list_users(admin_user)
    return UserListResponse(items=[UserResponse.from_user(user) for user in users])


@router.post(
    "/users",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_user(
    payload: CreateUserRequest,
    request: Request,
    _session: AuthenticatedSession = Depends(require_csrf),
    admin_user: User = Depends(require_admin),
    service: AuthService = Depends(get_auth_service),
) -> UserResponse:
    user = await service.create_user(
        admin_user,
        username=payload.username,
        password=payload.password,
        role=payload.role,
        enabled=payload.enabled,
        request_id=get_request_context(request).request_id,
    )
    return UserResponse.from_user(user)


@router.post("/users/{user_uid}/reset-password", response_model=UserResponse)
async def reset_password(
    user_uid: str,
    payload: ResetPasswordRequest,
    request: Request,
    _session: AuthenticatedSession = Depends(require_csrf),
    admin_user: User = Depends(require_admin),
    service: AuthService = Depends(get_auth_service),
) -> UserResponse:
    user = await service.reset_password(
        admin_user,
        user_uid,
        new_password=payload.new_password,
        request_id=get_request_context(request).request_id,
    )
    return UserResponse.from_user(user)


@router.post("/users/{user_uid}/enable", response_model=UserResponse)
async def enable_user(
    user_uid: str,
    request: Request,
    _session: AuthenticatedSession = Depends(require_csrf),
    admin_user: User = Depends(require_admin),
    service: AuthService = Depends(get_auth_service),
) -> UserResponse:
    user = await service.set_user_enabled(
        admin_user,
        user_uid,
        enabled=True,
        request_id=get_request_context(request).request_id,
    )
    return UserResponse.from_user(user)


@router.post("/users/{user_uid}/disable", response_model=UserResponse)
async def disable_user(
    user_uid: str,
    request: Request,
    _session: AuthenticatedSession = Depends(require_csrf),
    admin_user: User = Depends(require_admin),
    service: AuthService = Depends(get_auth_service),
) -> UserResponse:
    user = await service.set_user_enabled(
        admin_user,
        user_uid,
        enabled=False,
        request_id=get_request_context(request).request_id,
    )
    return UserResponse.from_user(user)


@router.post("/sessions/revoke", response_model=RevokeSessionsResponse)
async def revoke_sessions(
    payload: RevokeSessionsRequest,
    request: Request,
    _session: AuthenticatedSession = Depends(require_csrf),
    admin_user: User = Depends(require_admin),
    service: AuthService = Depends(get_auth_service),
) -> RevokeSessionsResponse:
    revoked = await service.revoke_user_sessions(
        admin_user,
        payload.user_uid,
        request_id=get_request_context(request).request_id,
    )
    return RevokeSessionsResponse(revoked_sessions=revoked)
