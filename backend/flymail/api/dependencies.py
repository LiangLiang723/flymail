"""Request-scoped identity, authentication, and tracing dependencies."""

from __future__ import annotations

from dataclasses import dataclass, replace
from urllib.parse import urlsplit

from fastapi import Depends, Request

from flymail.application.auth import (
    SESSION_COOKIE_NAME,
    AuthService,
    AuthenticatedSession,
)
from flymail.domain.errors import AuthenticationError, AuthorizationError, CsrfError
from flymail.repositories.users import User


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    user_uid: str
    username: str
    role: str

    def __post_init__(self) -> None:
        for field_name in ("user_uid", "username", "role"):
            value = str(getattr(self, field_name) or "").strip()
            if not value:
                raise ValueError(f"{field_name} is required")
            object.__setattr__(self, field_name, value)


@dataclass(frozen=True, slots=True)
class RequestContext:
    request_id: str
    trace_id: str
    actor: AuthenticatedUser | None = None

    def __post_init__(self) -> None:
        for field_name in ("request_id", "trace_id"):
            value = str(getattr(self, field_name) or "").strip()
            if not value:
                raise ValueError(f"{field_name} is required")
            object.__setattr__(self, field_name, value)


def get_request_context(request: Request) -> RequestContext:
    context = getattr(request.state, "context", None)
    if not isinstance(context, RequestContext):
        raise RuntimeError("request context is unavailable")
    return context


def set_authenticated_actor(
    request: Request,
    actor: AuthenticatedUser | None,
) -> RequestContext:
    context = get_request_context(request)
    updated = replace(context, actor=actor)
    request.state.context = updated
    return updated


def get_auth_service(request: Request) -> AuthService:
    service = getattr(request.app.state, "auth_service", None)
    if not isinstance(service, AuthService):
        raise RuntimeError("authentication service is unavailable")
    return service


async def require_session(request: Request) -> AuthenticatedSession:
    cached = getattr(request.state, "authenticated_session", None)
    if isinstance(cached, AuthenticatedSession):
        return cached
    cookie_value = request.cookies.get(SESSION_COOKIE_NAME, "")
    if not cookie_value:
        raise AuthenticationError("authentication required")
    session = await get_auth_service(request).authenticate(cookie_value)
    request.state.authenticated_session = session
    set_authenticated_actor(
        request,
        AuthenticatedUser(
            user_uid=session.user.id,
            username=session.user.username,
            role=session.user.role,
        ),
    )
    return session


async def require_user(
    session: AuthenticatedSession = Depends(require_session),
) -> User:
    return session.user


async def require_admin(user: User = Depends(require_user)) -> User:
    if user.role != "admin" or not user.enabled:
        raise AuthorizationError("administrator role is required")
    return user


def _origin_from_referer(referer: str) -> str:
    parsed = urlsplit(str(referer or ""))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


async def require_csrf(
    request: Request,
    session: AuthenticatedSession = Depends(require_session),
) -> AuthenticatedSession:
    origin = str(request.headers.get("origin") or "").strip()
    if not origin:
        origin = _origin_from_referer(request.headers.get("referer", ""))
    expected_origin = f"{request.url.scheme}://{request.url.netloc}"
    try:
        get_auth_service(request).validate_csrf(
            session,
            supplied_token=str(request.headers.get("x-csrf-token") or ""),
            origin=origin,
            expected_origin=expected_origin,
        )
    except CsrfError:
        raise
    except Exception:
        raise CsrfError("csrf validation failed") from None
    return session
