"""Local attachment and raw-message status, request, and streaming routes."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse, StreamingResponse

from flymail.api.dependencies import require_csrf, require_session
from flymail.api.schemas.content import (
    AttachmentMetadataResponse,
    ContentQueuedResponse,
    RawMessageStatusResponse,
)
from flymail.application.auth import AuthenticatedSession
from flymail.application.content import (
    ContentApiService,
    QueuedContent,
    StoredContent,
)
from flymail.domain.errors import ApiContractError


router = APIRouter(tags=["content"])


def _service(request: Request) -> ContentApiService:
    service = getattr(request.app.state, "content_api_service", None)
    if not isinstance(service, ContentApiService):
        raise RuntimeError("content API service is unavailable")
    return service


def _queued_response(value: QueuedContent) -> JSONResponse:
    payload = ContentQueuedResponse(
        resource_id=value.resource_id,
        state=value.state,
        job_id=value.job_id,
    )
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content=payload.model_dump(mode="json"),
        headers={"Cache-Control": "no-store"},
    )


def _content_disposition(filename: str) -> str:
    raw = str(filename or "download").replace("\\", "/").split("/")[-1]
    cleaned = "".join(
        character
        for character in raw
        if ord(character) >= 32 and character not in {'\x7f', '"'}
    ).strip()[:255]
    encoded = quote(cleaned or "download", safe="")
    return f"attachment; filename=\"download\"; filename*=UTF-8''{encoded}"


def _range_position(
    header: str,
    size: int,
    compression: str,
) -> tuple[int, int | None, int]:
    value = str(header or "").strip()
    if not value:
        return 0, None, 200
    if compression != "none":
        raise ApiContractError(
            "range_not_supported",
            "压缩缓存对象不支持范围下载",
            status_code=416,
        )
    if not value.startswith("bytes=") or "," in value:
        raise ApiContractError("invalid_range", "下载范围无效", status_code=416)
    raw_start, separator, raw_end = value[6:].partition("-")
    if not separator:
        raise ApiContractError("invalid_range", "下载范围无效", status_code=416)
    try:
        if raw_start:
            start = int(raw_start)
            end = int(raw_end) if raw_end else size - 1
        else:
            suffix = int(raw_end)
            if suffix < 1:
                raise ValueError
            start = max(size - suffix, 0)
            end = size - 1
    except ValueError:
        raise ApiContractError("invalid_range", "下载范围无效", status_code=416) from None
    if size < 1 or start < 0 or end < start or start >= size:
        raise ApiContractError(
            "invalid_range",
            "下载范围无效",
            status_code=416,
            details={"size": max(size, 0)},
        )
    return start, min(end, size - 1), 206


def _stream_response(
    request: Request,
    service: ContentApiService,
    content: StoredContent,
) -> StreamingResponse:
    start, end, status_code = _range_position(
        request.headers.get("range", ""),
        content.original_size_bytes,
        content.compression,
    )
    headers = {
        "Content-Type": content.content_type,
        "Content-Disposition": _content_disposition(content.filename),
        "Accept-Ranges": "bytes" if content.compression == "none" else "none",
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
    }
    if status_code == 206 and end is not None:
        headers["Content-Range"] = (
            f"bytes {start}-{end}/{content.original_size_bytes}"
        )
        headers["Content-Length"] = str(end - start + 1)
    elif content.compression == "none":
        headers["Content-Length"] = str(content.original_size_bytes)
    return StreamingResponse(
        service.stream(content, start=start, end=end),
        status_code=status_code,
        headers=headers,
    )


@router.get(
    "/api/v2/attachments/{attachment_id}",
    response_model=AttachmentMetadataResponse,
)
async def attachment_metadata(
    attachment_id: str,
    request: Request,
    session: AuthenticatedSession = Depends(require_session),
) -> AttachmentMetadataResponse:
    return await _service(request).attachment_metadata(session, attachment_id)


@router.post("/api/v2/attachments/{attachment_id}/request")
async def request_attachment(
    attachment_id: str,
    request: Request,
    session: AuthenticatedSession = Depends(require_csrf),
) -> JSONResponse:
    return _queued_response(
        await _service(request).request_attachment(session, attachment_id)
    )


@router.get("/api/v2/attachments/{attachment_id}/content")
async def attachment_content(
    attachment_id: str,
    request: Request,
    session: AuthenticatedSession = Depends(require_session),
):
    service = _service(request)
    result = await service.resolve_attachment(session, attachment_id)
    if isinstance(result, QueuedContent):
        return _queued_response(result)
    return _stream_response(request, service, result)


@router.get(
    "/api/v2/messages/{message_id}/raw",
    response_model=RawMessageStatusResponse,
)
async def raw_status(
    message_id: str,
    request: Request,
    session: AuthenticatedSession = Depends(require_session),
) -> RawMessageStatusResponse:
    return await _service(request).raw_status(session, message_id)


@router.post("/api/v2/messages/{message_id}/raw/request")
async def request_raw(
    message_id: str,
    request: Request,
    session: AuthenticatedSession = Depends(require_csrf),
) -> JSONResponse:
    return _queued_response(await _service(request).request_raw(session, message_id))


@router.get("/api/v2/messages/{message_id}/raw/content")
async def raw_content(
    message_id: str,
    request: Request,
    session: AuthenticatedSession = Depends(require_session),
):
    service = _service(request)
    result = await service.resolve_raw(session, message_id)
    if isinstance(result, QueuedContent):
        return _queued_response(result)
    return _stream_response(request, service, result)
