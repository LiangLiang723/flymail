"""Structured local-search request and response schemas."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SearchFilter(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    keyword: str | None = Field(default=None, max_length=512)
    from_addresses: tuple[str, ...] = Field(default=(), max_length=50)
    to_addresses: tuple[str, ...] = Field(default=(), max_length=50)
    date_from: float | None = Field(default=None, ge=0)
    date_to: float | None = Field(default=None, ge=0)
    account_ids: tuple[str, ...] = Field(default=(), max_length=50)
    mailbox_ids: tuple[str, ...] = Field(default=(), max_length=50)
    label_ids: tuple[str, ...] = Field(default=(), max_length=50)
    is_read: bool | None = None
    is_starred: bool | None = None
    has_attachment: bool | None = None
    min_size_bytes: int | None = Field(default=None, ge=0)
    max_size_bytes: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_ranges(self) -> "SearchFilter":
        if self.date_from is not None and self.date_to is not None:
            if self.date_from > self.date_to:
                raise ValueError("date_from must not exceed date_to")
        if self.min_size_bytes is not None and self.max_size_bytes is not None:
            if self.min_size_bytes > self.max_size_bytes:
                raise ValueError("min_size_bytes must not exceed max_size_bytes")
        return self

    def has_condition(self) -> bool:
        return any(
            (
                bool(str(self.keyword or "").strip()),
                bool(self.from_addresses),
                bool(self.to_addresses),
                self.date_from is not None,
                self.date_to is not None,
                bool(self.account_ids),
                bool(self.mailbox_ids),
                bool(self.label_ids),
                self.is_read is not None,
                self.is_starred is not None,
                self.has_attachment is not None,
                self.min_size_bytes is not None,
                self.max_size_bytes is not None,
            )
        )


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filters: SearchFilter
    limit: int = Field(default=20, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=2048)


class SearchResultItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    thread_id: str
    matched_message_id: str
    matched_field: str
    subject: str
    snippet: str
    received_at: float
    account_ids: tuple[str, ...]
    unread: bool
    starred: bool
    has_attachment: bool


class SearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[SearchResultItem, ...]
    next_cursor: str | None
    fulltext_parser: str


class SearchSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str
    value: str
    label: str


class SuggestionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[SearchSuggestion, ...]


class SearchHistoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sequence_id: int
    filters: SearchFilter
    created_at: float


class SearchHistoryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[SearchHistoryItem, ...]


class SavedSearchCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=191)
    filters: SearchFilter
    is_pinned: bool = False


class SavedSearchUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=191)
    filters: SearchFilter | None = None
    is_pinned: bool | None = None


class SavedSearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    name: str
    filters: SearchFilter
    is_pinned: bool
    created_at: float
    updated_at: float


class SavedSearchListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[SavedSearchResponse, ...]
