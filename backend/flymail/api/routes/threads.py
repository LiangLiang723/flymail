"""Tenant-scoped thread list, detail, and local body routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from flymail.api.dependencies import require_session
from flymail.api.schemas.threads import (
    BodyQueuedResponse,
    ThreadDetailResponse,
    ThreadListResponse,
)
from flymail.application.auth import AuthenticatedSession
from flymail.application.thread_queries import (
    BodyContent,
    BodyQueue,
    ThreadQueryService,
)


router = APIRouter(tags=["threads"])


def _service(request: Request) -> ThreadQueryService:
    service = getattr(request.app.state, "thread_query_service", None)
    if not isinstance(service, ThreadQueryService):
        raise RuntimeError("thread query service is unavailable")
    return service


@router.get("/api/v2/threads", response_model=ThreadListResponse)
async def list_threads(
    request: Request,
    mailbox: str = Query(default="inbox", min_length=1, max_length=64),
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=2048),
    account_id: str | None = Query(default=None, max_length=64),
    native_label: str | None = Query(default=None, max_length=64),
    unread: bool | None = Query(default=None),
    starred: bool | None = Query(default=None),
    has_attachment: bool | None = Query(default=None),
    session: AuthenticatedSession = Depends(require_session),
) -> ThreadListResponse:
    return await _service(request).list_threads(
        session,
        semantic_mailbox=mailbox,
        limit=limit,
        cursor=cursor,
        account_id=account_id,
        native_label=native_label,
        unread=unread,
        starred=starred,
        has_attachment=has_attachment,
    )


@router.get("/api/v2/threads/{thread_id}", response_model=ThreadDetailResponse)
async def get_thread(
    thread_id: str,
    request: Request,
    session: AuthenticatedSession = Depends(require_session),
) -> ThreadDetailResponse:
    return await _service(request).get_thread(session, thread_id)


@router.get("/api/v2/messages/{message_id}/body")
async def get_message_body(
    message_id: str,
    request: Request,
    session: AuthenticatedSession = Depends(require_session),
):
    service = _service(request)
    result = await service.resolve_body(session, message_id)
    if isinstance(result, BodyQueue):
        payload = BodyQueuedResponse(
            message_id=result.message_id,
            state=result.state,
            job_id=result.job_id,
        )
        return JSONResponse(
            status_code=202,
            content=payload.model_dump(mode="json"),
            headers={"Cache-Control": "no-store"},
        )
    if not isinstance(result, BodyContent):
        raise RuntimeError("unexpected body query result")
    return StreamingResponse(
        service.stream_body(result),
        headers={
            "Content-Type": result.content_type,
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
