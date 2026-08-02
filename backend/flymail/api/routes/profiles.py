"""Current-user profile, avatar, and account-icon routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from flymail.api.dependencies import require_csrf, require_session
from flymail.api.schemas.personal import (
    AccountIconRequest,
    AccountIconResponse,
    ProfileResponse,
    ProfileUpdateRequest,
)
from flymail.application.auth import AuthenticatedSession
from flymail.application.personal import MAX_PROFILE_IMAGE_BYTES, PersonalService
from flymail.domain.errors import ApiContractError


router = APIRouter(tags=["personal"])


def _service(request: Request) -> PersonalService:
    service = getattr(request.app.state, "personal_service", None)
    if not isinstance(service, PersonalService):
        raise RuntimeError("personal service is unavailable")
    return service


async def _read_bounded(request: Request) -> bytes:
    content_length = request.headers.get("content-length", "")
    if content_length.isdigit() and int(content_length) > MAX_PROFILE_IMAGE_BYTES:
        raise ApiContractError("image_too_large", "image file exceeds 5 MiB", status_code=422)
    output = bytearray()
    async for chunk in request.stream():
        output.extend(chunk)
        if len(output) > MAX_PROFILE_IMAGE_BYTES:
            raise ApiContractError("image_too_large", "image file exceeds 5 MiB", status_code=422)
    return bytes(output)


@router.get("/api/v2/profile", response_model=ProfileResponse)
async def get_profile(
    request: Request,
    session: AuthenticatedSession = Depends(require_session),
) -> ProfileResponse:
    return await _service(request).profile(session)


@router.patch("/api/v2/profile", response_model=ProfileResponse)
async def update_profile(
    payload: ProfileUpdateRequest,
    request: Request,
    session: AuthenticatedSession = Depends(require_csrf),
) -> ProfileResponse:
    return await _service(request).update_profile(
        session,
        payload.nickname,
        request_id=request.state.context.request_id,
    )


@router.post("/api/v2/profile/avatar", response_model=ProfileResponse)
async def upload_avatar(
    request: Request,
    session: AuthenticatedSession = Depends(require_csrf),
) -> ProfileResponse:
    return await _service(request).upload_avatar(
        session,
        await _read_bounded(request),
        request_id=request.state.context.request_id,
    )


@router.get("/api/v2/profile/avatar")
async def read_avatar(
    request: Request,
    session: AuthenticatedSession = Depends(require_session),
) -> Response:
    return Response(
        await _service(request).avatar_bytes(session),
        media_type="image/webp",
        headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"},
    )


@router.put(
    "/api/v2/accounts/{account_id}/icon",
    response_model=AccountIconResponse,
)
async def set_account_icon(
    account_id: str,
    payload: AccountIconRequest,
    request: Request,
    session: AuthenticatedSession = Depends(require_csrf),
) -> AccountIconResponse:
    return await _service(request).set_account_icon(
        session,
        account_id,
        mode=payload.mode,
        value=payload.value,
        request_id=request.state.context.request_id,
    )


@router.post(
    "/api/v2/accounts/{account_id}/icon/upload",
    response_model=AccountIconResponse,
)
async def upload_account_icon(
    account_id: str,
    request: Request,
    session: AuthenticatedSession = Depends(require_csrf),
) -> AccountIconResponse:
    return await _service(request).upload_account_icon(
        session,
        account_id,
        await _read_bounded(request),
        request_id=request.state.context.request_id,
    )


@router.get("/api/v2/accounts/{account_id}/icon/content")
async def account_icon_content(
    account_id: str,
    request: Request,
    session: AuthenticatedSession = Depends(require_session),
) -> Response:
    return Response(
        await _service(request).account_icon_bytes(session, account_id),
        media_type="image/webp",
        headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"},
    )
