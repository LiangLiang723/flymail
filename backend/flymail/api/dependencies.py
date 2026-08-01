"""Request-scoped identity and tracing dependencies for FlyMail V2."""

from __future__ import annotations

from dataclasses import dataclass, replace

from fastapi import Request


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
