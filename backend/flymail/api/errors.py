"""Stable and secret-safe error responses for FlyMail V2."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from flymail.api.dependencies import RequestContext
from flymail.api.middleware import safe_request_id
from flymail.api.schemas.common import ErrorBody, ErrorEnvelope
from flymail.domain.errors import (
    ApiContractError,
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    CsrfError,
    InvalidCredentialsError,
    NotFoundError,
    RateLimitError,
    UnsafeEndpointError,
    UnsupportedProviderError,
)


logger = logging.getLogger("flymail.v2.api")


def _request_id(request: Request) -> str:
    context = getattr(request.state, "context", None)
    if isinstance(context, RequestContext):
        return context.request_id
    return safe_request_id(None)


def error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    request_id = _request_id(request)
    envelope = ErrorEnvelope(
        error=ErrorBody(
            code=code,
            message=message,
            request_id=request_id,
            details=details,
        )
    )
    return JSONResponse(
        status_code=status_code,
        content=envelope.model_dump(mode="json"),
        headers={
            "X-Request-ID": request_id,
            "Server-Timing": "total;dur=0.000, db;dur=0.000, serialize;dur=0.000",
        },
    )


async def api_contract_error_handler(
    request: Request,
    exc: ApiContractError,
) -> JSONResponse:
    return error_response(
        request,
        status_code=exc.status_code,
        code=exc.code,
        message=exc.public_message,
        details=exc.details,
    )


async def authentication_error_handler(
    request: Request,
    _exc: AuthenticationError,
) -> JSONResponse:
    response = error_response(
        request,
        status_code=401,
        code="authentication_required",
        message="请先登录",
    )
    response.headers["WWW-Authenticate"] = "Session"
    return response


async def invalid_credentials_error_handler(
    request: Request,
    _exc: InvalidCredentialsError,
) -> JSONResponse:
    return error_response(
        request,
        status_code=401,
        code="invalid_credentials",
        message="用户名或密码错误",
    )


async def csrf_error_handler(
    request: Request,
    _exc: CsrfError,
) -> JSONResponse:
    return error_response(
        request,
        status_code=403,
        code="csrf_failed",
        message="请求来源或安全令牌无效",
    )


async def rate_limit_error_handler(
    request: Request,
    _exc: RateLimitError,
) -> JSONResponse:
    response = error_response(
        request,
        status_code=429,
        code="rate_limited",
        message="登录尝试过多，请稍后再试",
    )
    response.headers["Retry-After"] = "300"
    return response


async def unsafe_endpoint_error_handler(
    request: Request,
    _exc: UnsafeEndpointError,
) -> JSONResponse:
    return error_response(
        request,
        status_code=422,
        code="unsafe_endpoint",
        message="邮箱服务器或代理地址不允许访问",
    )


async def unsupported_provider_error_handler(
    request: Request,
    _exc: UnsupportedProviderError,
) -> JSONResponse:
    return error_response(
        request,
        status_code=422,
        code="unsupported_provider",
        message="不支持该邮箱服务商",
    )


async def authorization_error_handler(
    request: Request,
    _exc: AuthorizationError,
) -> JSONResponse:
    return error_response(
        request,
        status_code=403,
        code="authorization_denied",
        message="没有权限执行此操作",
    )


async def conflict_error_handler(
    request: Request,
    _exc: ConflictError,
) -> JSONResponse:
    return error_response(
        request,
        status_code=409,
        code="conflict",
        message="请求与当前状态冲突",
    )


async def not_found_error_handler(
    request: Request,
    _exc: NotFoundError,
) -> JSONResponse:
    return error_response(
        request,
        status_code=404,
        code="not_found",
        message="请求的资源不存在",
    )


async def validation_error_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    fields = [
        {
            "location": [str(item) for item in error.get("loc", ())],
            "message": "invalid value",
            "type": str(error.get("type") or "validation_error")[:128],
        }
        for error in exc.errors()
    ]
    return error_response(
        request,
        status_code=422,
        code="validation_error",
        message="请求参数无效",
        details={"fields": fields},
    )


def _resolve_frontend_file(ui_dir: Path, request_path: str) -> Path | None:
    relative = str(request_path).strip().replace("\\", "/").lstrip("/")
    if not relative or "\x00" in relative:
        return None
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    resolved = (ui_dir / candidate).resolve()
    if not resolved.is_relative_to(ui_dir) or not resolved.is_file():
        return None
    return resolved


def _frontend_not_found_response(request: Request) -> FileResponse | None:
    if request.method not in {"GET", "HEAD"}:
        return None

    ui_dir = getattr(request.app.state, "frontend_ui_dir", None)
    index_path = getattr(request.app.state, "frontend_index_path", None)
    if not isinstance(ui_dir, Path) or not isinstance(index_path, Path):
        return None

    normalized = request.url.path.strip().replace("\\", "/").lstrip("/")
    if normalized == "api" or normalized.startswith("api/"):
        return None

    asset_path = _resolve_frontend_file(ui_dir, normalized)
    if asset_path is not None:
        return FileResponse(asset_path)
    if normalized.startswith("assets/") or Path(normalized).suffix:
        return None
    return FileResponse(index_path)


async def http_error_handler(
    request: Request,
    exc: StarletteHTTPException,
) -> Response:
    status_code = int(exc.status_code)
    if status_code == 404:
        frontend_response = _frontend_not_found_response(request)
        if frontend_response is not None:
            return frontend_response
        code, message = "not_found", "请求的资源不存在"
    elif status_code == 405:
        code, message = "method_not_allowed", "请求方法不受支持"
    else:
        code, message = "http_error", "请求无法处理"
    return error_response(
        request,
        status_code=status_code,
        code=code,
        message=message,
    )


async def unexpected_error_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    logger.error(
        "unhandled API error request_id=%s error_class=%s",
        _request_id(request),
        type(exc).__name__,
    )
    return error_response(
        request,
        status_code=500,
        code="internal_error",
        message="服务暂时不可用",
    )
