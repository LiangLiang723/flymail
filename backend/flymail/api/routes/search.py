"""Structured local search, suggestions, history, and saved-search routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response, status

from flymail.api.dependencies import require_csrf, require_session
from flymail.api.schemas.search import (
    SavedSearchCreateRequest,
    SavedSearchListResponse,
    SavedSearchResponse,
    SavedSearchUpdateRequest,
    SearchHistoryResponse,
    SearchRequest,
    SearchResponse,
    SuggestionResponse,
)
from flymail.application.auth import AuthenticatedSession
from flymail.application.search_queries import SearchQueryService


router = APIRouter(tags=["search"])


def _service(request: Request) -> SearchQueryService:
    service = getattr(request.app.state, "search_query_service", None)
    if not isinstance(service, SearchQueryService):
        raise RuntimeError("search query service is unavailable")
    return service


@router.post("/api/v2/search", response_model=SearchResponse)
async def search(
    payload: SearchRequest,
    request: Request,
    session: AuthenticatedSession = Depends(require_session),
) -> SearchResponse:
    return await _service(request).search(
        session,
        payload.filters,
        limit=payload.limit,
        cursor=payload.cursor,
    )


@router.get("/api/v2/search/suggestions", response_model=SuggestionResponse)
async def suggestions(
    request: Request,
    q: str = Query(default="", max_length=191),
    session: AuthenticatedSession = Depends(require_session),
) -> SuggestionResponse:
    return SuggestionResponse(items=await _service(request).suggestions(session, q))


@router.get("/api/v2/search/history", response_model=SearchHistoryResponse)
async def history(
    request: Request,
    session: AuthenticatedSession = Depends(require_session),
) -> SearchHistoryResponse:
    return SearchHistoryResponse(items=await _service(request).history(session))


@router.delete("/api/v2/search/history", status_code=status.HTTP_204_NO_CONTENT)
async def clear_history(
    request: Request,
    session: AuthenticatedSession = Depends(require_csrf),
) -> Response:
    await _service(request).clear_history(session)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/api/v2/saved-searches",
    response_model=SavedSearchResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_saved_search(
    payload: SavedSearchCreateRequest,
    request: Request,
    session: AuthenticatedSession = Depends(require_csrf),
) -> SavedSearchResponse:
    return await _service(request).create_saved(
        session,
        name=payload.name,
        filters=payload.filters,
        is_pinned=payload.is_pinned,
    )


@router.get("/api/v2/saved-searches", response_model=SavedSearchListResponse)
async def list_saved_searches(
    request: Request,
    session: AuthenticatedSession = Depends(require_session),
) -> SavedSearchListResponse:
    return SavedSearchListResponse(items=await _service(request).list_saved(session))


@router.patch("/api/v2/saved-searches/{saved_id}", response_model=SavedSearchResponse)
async def update_saved_search(
    saved_id: str,
    payload: SavedSearchUpdateRequest,
    request: Request,
    session: AuthenticatedSession = Depends(require_csrf),
) -> SavedSearchResponse:
    return await _service(request).update_saved(
        session,
        saved_id,
        name=payload.name,
        filters=payload.filters,
        is_pinned=payload.is_pinned,
    )


@router.delete(
    "/api/v2/saved-searches/{saved_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_saved_search(
    saved_id: str,
    request: Request,
    session: AuthenticatedSession = Depends(require_csrf),
) -> Response:
    await _service(request).delete_saved(session, saved_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
