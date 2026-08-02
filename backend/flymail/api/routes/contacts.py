"""Tenant-scoped contact CRUD routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response, status

from flymail.api.dependencies import require_csrf, require_session
from flymail.api.schemas.personal import QuickAddContactRequest
from flymail.api.schemas.settings_contacts import (
    ContactCreateRequest,
    ContactListResponse,
    ContactResponse,
    ContactUpdateRequest,
)
from flymail.application.auth import AuthenticatedSession
from flymail.application.settings_contacts import SettingsContactsService


router = APIRouter(tags=["contacts"])


def _service(request: Request) -> SettingsContactsService:
    service = getattr(request.app.state, "settings_contacts_service", None)
    if not isinstance(service, SettingsContactsService):
        raise RuntimeError("contacts service is unavailable")
    return service


@router.post(
    "/api/v2/contacts",
    response_model=ContactResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_contact(
    payload: ContactCreateRequest,
    request: Request,
    session: AuthenticatedSession = Depends(require_csrf),
) -> ContactResponse:
    return await _service(request).create_contact(
        session,
        display_name=payload.display_name,
        primary_email=payload.primary_email,
        emails=payload.emails,
        request_id=request.state.context.request_id,
    )


@router.post(
    "/api/v2/contacts/quick-add",
    response_model=ContactResponse,
    status_code=status.HTTP_201_CREATED,
)
async def quick_add_contact(
    payload: QuickAddContactRequest,
    request: Request,
    session: AuthenticatedSession = Depends(require_csrf),
) -> ContactResponse:
    return await _service(request).quick_add_from_message(
        session,
        payload.message_id,
        request_id=request.state.context.request_id,
    )


@router.get("/api/v2/contacts/autocomplete", response_model=ContactListResponse)
async def autocomplete_contacts(
    request: Request,
    q: str = Query(default="", max_length=191),
    limit: int = Query(default=20, ge=1, le=50),
    session: AuthenticatedSession = Depends(require_session),
) -> ContactListResponse:
    return ContactListResponse(
        items=await _service(request).list_contacts(
            session,
            query=q,
            limit=limit,
        )
    )


@router.get("/api/v2/contacts", response_model=ContactListResponse)
async def list_contacts(
    request: Request,
    q: str = Query(default="", max_length=191),
    limit: int = Query(default=50, ge=1, le=100),
    session: AuthenticatedSession = Depends(require_session),
) -> ContactListResponse:
    return ContactListResponse(
        items=await _service(request).list_contacts(
            session,
            query=q,
            limit=limit,
        )
    )


@router.patch("/api/v2/contacts/{contact_id}", response_model=ContactResponse)
async def update_contact(
    contact_id: str,
    payload: ContactUpdateRequest,
    request: Request,
    session: AuthenticatedSession = Depends(require_csrf),
) -> ContactResponse:
    return await _service(request).update_contact(
        session,
        contact_id,
        display_name=payload.display_name,
        primary_email=payload.primary_email,
        emails=payload.emails,
        request_id=request.state.context.request_id,
    )


@router.delete("/api/v2/contacts/{contact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_contact(
    contact_id: str,
    request: Request,
    session: AuthenticatedSession = Depends(require_csrf),
) -> Response:
    await _service(request).delete_contact(
        session,
        contact_id,
        request_id=request.state.context.request_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
