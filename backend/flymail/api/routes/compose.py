"""Versioned draft, attachment import, compose template, and send routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status

from flymail.api.dependencies import require_csrf, require_session
from flymail.api.schemas.compose import (
    CancelSendRequest,
    ComposeTemplateResponse,
    DraftAttachmentResponse,
    DraftCreateRequest,
    DraftResponse,
    DraftUpdateRequest,
    ImportAttachmentRequest,
    SendDraftRequest,
    SendDraftResponse,
    StorageRootListResponse,
)
from flymail.application.auth import AuthenticatedSession
from flymail.application.compose import ComposeService


router = APIRouter(tags=["compose"])


def _service(request: Request) -> ComposeService:
    service = getattr(request.app.state, "compose_service", None)
    if not isinstance(service, ComposeService):
        raise RuntimeError("compose service is unavailable")
    return service


@router.post(
    "/api/v2/drafts",
    response_model=DraftResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_draft(
    payload: DraftCreateRequest,
    request: Request,
    session: AuthenticatedSession = Depends(require_csrf),
) -> DraftResponse:
    return await _service(request).create_draft(
        session,
        account_id=payload.account_id,
        identity_id=payload.identity_id,
        thread_id=payload.thread_id,
        reply_to_message_id=payload.reply_to_message_id,
        subject=payload.subject,
        body_html=payload.body_html,
        body_text=payload.body_text,
        recipients=payload.recipients,
        scheduled_at=payload.scheduled_at,
    )


@router.get("/api/v2/drafts/{draft_id}", response_model=DraftResponse)
async def get_draft(
    draft_id: str,
    request: Request,
    session: AuthenticatedSession = Depends(require_session),
) -> DraftResponse:
    return await _service(request).get_draft(session, draft_id)


@router.put("/api/v2/drafts/{draft_id}", response_model=DraftResponse)
async def update_draft(
    draft_id: str,
    payload: DraftUpdateRequest,
    request: Request,
    session: AuthenticatedSession = Depends(require_csrf),
) -> DraftResponse:
    return await _service(request).update_draft(
        session,
        draft_id,
        expected_version=payload.expected_version,
        account_id=payload.account_id,
        identity_id=payload.identity_id,
        subject=payload.subject,
        body_html=payload.body_html,
        body_text=payload.body_text,
        recipients=payload.recipients,
        scheduled_at=payload.scheduled_at,
    )


@router.delete("/api/v2/drafts/{draft_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_draft(
    draft_id: str,
    request: Request,
    session: AuthenticatedSession = Depends(require_csrf),
) -> Response:
    await _service(request).delete_draft(session, draft_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/api/v2/drafts/{draft_id}/attachments",
    response_model=DraftAttachmentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_attachment(
    draft_id: str,
    request: Request,
    x_filename: str = Header(default="attachment", max_length=1024),
    session: AuthenticatedSession = Depends(require_csrf),
) -> DraftAttachmentResponse:
    raw_length = request.headers.get("content-length")
    expected_size = int(raw_length) if raw_length and raw_length.isdigit() else None
    return await _service(request).add_attachment(
        session,
        draft_id,
        filename=x_filename,
        content_type=request.headers.get("content-type", "application/octet-stream"),
        chunks=request.stream(),
        expected_size=expected_size,
    )


@router.delete(
    "/api/v2/drafts/{draft_id}/attachments/{attachment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_attachment(
    draft_id: str,
    attachment_id: str,
    request: Request,
    session: AuthenticatedSession = Depends(require_csrf),
) -> Response:
    await _service(request).remove_attachment(session, draft_id, attachment_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/api/v2/storage-roots", response_model=StorageRootListResponse)
async def storage_roots(
    request: Request,
    session: AuthenticatedSession = Depends(require_session),
) -> StorageRootListResponse:
    return StorageRootListResponse(items=await _service(request).storage_roots(session))


@router.post(
    "/api/v2/drafts/{draft_id}/attachments/import",
    response_model=DraftAttachmentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def import_attachment(
    draft_id: str,
    payload: ImportAttachmentRequest,
    request: Request,
    session: AuthenticatedSession = Depends(require_csrf),
) -> DraftAttachmentResponse:
    return await _service(request).import_attachment(
        session,
        draft_id,
        root_id=payload.root_id,
        relative_path=payload.relative_path,
    )


@router.get(
    "/api/v2/messages/{message_id}/compose-template",
    response_model=ComposeTemplateResponse,
)
async def compose_template(
    message_id: str,
    request: Request,
    mode: str = Query(default="reply", pattern="^(reply|forward)$"),
    session: AuthenticatedSession = Depends(require_session),
) -> ComposeTemplateResponse:
    return await _service(request).compose_template(session, message_id, mode=mode)


@router.post(
    "/api/v2/drafts/{draft_id}/send",
    response_model=SendDraftResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def send_draft(
    draft_id: str,
    payload: SendDraftRequest,
    request: Request,
    session: AuthenticatedSession = Depends(require_csrf),
) -> SendDraftResponse:
    result = await _service(request).queue_send(
        session,
        draft_id,
        idempotency_key=payload.idempotency_key,
    )
    return SendDraftResponse(
        draft_id=result.draft_id,
        operation_id=result.operation_id,
        job_id=result.job_id,
        message_id_header=result.message_id_header,
    )


@router.post(
    "/api/v2/drafts/{draft_id}/cancel-send",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def cancel_send(
    draft_id: str,
    payload: CancelSendRequest,
    request: Request,
    session: AuthenticatedSession = Depends(require_csrf),
) -> Response:
    await _service(request).cancel_send(
        session,
        draft_id,
        operation_id=payload.operation_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
