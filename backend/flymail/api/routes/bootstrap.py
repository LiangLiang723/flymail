"""Authenticated single-request Bootstrap route for FlyMail V2."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from flymail.api.dependencies import require_session
from flymail.application.auth import AuthenticatedSession
from flymail.application.bootstrap import BootstrapResponse, BootstrapService


router = APIRouter(tags=["bootstrap"])


@router.get("/api/v2/bootstrap", response_model=BootstrapResponse)
async def bootstrap(
    request: Request,
    response: Response,
    session: AuthenticatedSession = Depends(require_session),
) -> BootstrapResponse:
    service = getattr(request.app.state, "bootstrap_service", None)
    if not isinstance(service, BootstrapService):
        raise RuntimeError("bootstrap service is unavailable")
    response.headers["Cache-Control"] = "no-store"
    return await service.load(session)
