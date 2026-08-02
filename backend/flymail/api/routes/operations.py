"""Local-first mail operation and undo routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status

from flymail.api.dependencies import require_csrf
from flymail.api.schemas.operations import (
    MarkAllReadRequest,
    MarkAllReadResponse,
    OperationAcceptedResponse,
    OperationInitialStatus,
    OperationRequest,
    PermanentDeleteConfirmationRequest,
    PermanentDeleteConfirmationResponse,
    UndoOperationRequest,
    UndoOperationResponse,
)
from flymail.application.auth import AuthenticatedSession
from flymail.application.operations import MailOperationApiService


router = APIRouter(tags=["operations"])


def _service(request: Request) -> MailOperationApiService:
    service = getattr(request.app.state, "mail_operation_api_service", None)
    if not isinstance(service, MailOperationApiService):
        raise RuntimeError("mail operation service is unavailable")
    return service


@router.post(
    "/api/v2/operations",
    response_model=OperationAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_operation(
    payload: OperationRequest,
    request: Request,
    session: AuthenticatedSession = Depends(require_csrf),
) -> OperationAcceptedResponse:
    result = await _service(request).create(
        session,
        target_type=payload.target_type,
        target_id=payload.target_id,
        operation_type=payload.operation_type,
        desired_state=payload.desired_state,
        idempotency_key=payload.idempotency_key,
        confirmation_token=payload.confirmation_token,
    )
    return OperationAcceptedResponse(
        operation_group_id=result.operation_group_id,
        operation_ids=result.operation_ids,
        items=tuple(
            OperationInitialStatus(operation_id=operation_id)
            for operation_id in result.operation_ids
        ),
    )


@router.post(
    "/api/v2/operations/permanent-delete-confirmation",
    response_model=PermanentDeleteConfirmationResponse,
)
async def permanent_delete_confirmation(
    payload: PermanentDeleteConfirmationRequest,
    request: Request,
    session: AuthenticatedSession = Depends(require_csrf),
) -> PermanentDeleteConfirmationResponse:
    result = await _service(request).issue_delete_confirmation(
        session,
        target_type=payload.target_type,
        target_id=payload.target_id,
    )
    return PermanentDeleteConfirmationResponse(
        confirmation_token=result.token,
        expires_at=result.expires_at,
    )


@router.post(
    "/api/v2/operations/{operation_id}/undo",
    response_model=UndoOperationResponse,
)
async def undo_operation(
    operation_id: str,
    payload: UndoOperationRequest,
    request: Request,
    session: AuthenticatedSession = Depends(require_csrf),
) -> UndoOperationResponse:
    result = await _service(request).undo(
        session,
        operation_id,
        idempotency_key=payload.idempotency_key,
    )
    return UndoOperationResponse(
        operation_id=result.operation_id,
        status=result.status,
    )


@router.post(
    "/api/v2/operations/mark-all-read",
    response_model=MarkAllReadResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def mark_all_read(
    payload: MarkAllReadRequest,
    request: Request,
    session: AuthenticatedSession = Depends(require_csrf),
) -> MarkAllReadResponse:
    result = await _service(request).mark_all_read(
        session,
        semantic_mailbox=payload.semantic_mailbox,
        account_id=payload.account_id,
        native_label=payload.native_label,
        idempotency_key=payload.idempotency_key,
    )
    return MarkAllReadResponse(
        bulk_operation_id=result.bulk_operation_id,
        job_id=result.job_id,
    )
