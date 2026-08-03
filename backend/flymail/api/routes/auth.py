"""Local authentication routes for FlyMail V2."""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Request, Response

from flymail.api.dependencies import (
    get_auth_service,
    get_request_context,
    require_csrf,
    require_session,
)
from flymail.api.schemas.auth import (
    AuthResponse,
    LoginRequest,
    OkResponse,
    PasswordChangeRequest,
    UserResponse,
)
from flymail.application.auth import (
    SESSION_COOKIE_NAME,
    AuthService,
    AuthenticatedSession,
)


router = APIRouter(prefix="/api/v2/auth", tags=["authentication"])


def _source(request: Request) -> str:
    return request.client.host if request.client is not None else "unknown"


def _set_session_cookie(
    response: Response,
    *,
    value: str,
    expires_at: float,
    secure: bool,
) -> None:
    max_age = max(int(expires_at - time.time()), 1)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        value,
        max_age=max_age,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/api/v2",
    )


@router.post("/login", response_model=AuthResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    service: AuthService = Depends(get_auth_service),
) -> AuthResponse:
    result = await service.login(
        username=payload.username,
        password=payload.password,
        source=_source(request),
        request_id=get_request_context(request).request_id,
    )
    _set_session_cookie(
        response,
        value=result.cookie_value,
        expires_at=result.expires_at,
        secure=request.url.scheme.casefold() == "https",
    )
    return AuthResponse(
        user=UserResponse.from_user(result.user),
        csrf_token=result.csrf_token,
    )


@router.get("/me", response_model=AuthResponse)
async def me(
    session: AuthenticatedSession = Depends(require_session),
) -> AuthResponse:
    return AuthResponse(
        user=UserResponse.from_user(session.user),
        csrf_token=session.csrf_token,
    )


@router.post("/logout", response_model=OkResponse)
async def logout(
    request: Request,
    response: Response,
    session: AuthenticatedSession = Depends(require_csrf),
    service: AuthService = Depends(get_auth_service),
) -> OkResponse:
    await service.logout(
        session,
        request_id=get_request_context(request).request_id,
    )
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        path="/api/v2",
        secure=request.url.scheme.casefold() == "https",
        httponly=True,
        samesite="lax",
    )
    return OkResponse()


@router.post("/password", response_model=AuthResponse)
async def change_password(
    payload: PasswordChangeRequest,
    request: Request,
    session: AuthenticatedSession = Depends(require_csrf),
    service: AuthService = Depends(get_auth_service),
) -> AuthResponse:
    user = await service.change_password(
        session,
        current_password=payload.current_password,
        new_password=payload.new_password,
        revoke_other_sessions=payload.revoke_other_sessions,
        request_id=get_request_context(request).request_id,
    )
    return AuthResponse(
        user=UserResponse.from_user(user),
        csrf_token=session.csrf_token,
    )
