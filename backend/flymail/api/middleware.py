"""Safe request context and timing middleware for FlyMail V2."""

from __future__ import annotations

import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from flymail.api.dependencies import RequestContext
from flymail.domain.ids import new_id
from flymail.observability.timing import RequestTiming


_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$")


def safe_request_id(value: str | None) -> str:
    normalized = str(value or "").strip()
    if _REQUEST_ID_PATTERN.fullmatch(normalized):
        return normalized
    return new_id("req")


class RequestContextMiddleware:
    """Attach immutable request context and safe timing headers."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        perf_counter: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.app = app
        self.perf_counter = perf_counter

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(raw=scope.get("headers", []))
        request_id = safe_request_id(headers.get("x-request-id"))
        state: dict[str, Any] = scope.setdefault("state", {})
        state["context"] = RequestContext(
            request_id=request_id,
            trace_id=new_id("trc"),
            actor=None,
        )
        state["db_time_ms"] = 0.0
        state["object_time_ms"] = 0.0
        state["serialization_time_ms"] = 0.0
        timing = RequestTiming(perf_counter=self.perf_counter)
        state["request_timing"] = timing

        async def send_with_context(message: Message) -> None:
            if message["type"] == "http.response.start":
                timing.record_db(float(state.get("db_time_ms", 0.0)))
                timing.record_object(float(state.get("object_time_ms", 0.0)))
                timing.record_serialize(
                    float(state.get("serialization_time_ms", 0.0))
                )
                values = timing.finish()
                response_headers = MutableHeaders(scope=message)
                response_headers["X-Request-ID"] = request_id
                response_headers["Server-Timing"] = (
                    f"total;dur={values['total_ms']:.3f}, "
                    f"db;dur={values['db_ms']:.3f}, "
                    f"object;dur={values['object_ms']:.3f}, "
                    f"serialize;dur={values['serialize_ms']:.3f}"
                )
            await send(message)

        await self.app(scope, receive, send_with_context)
