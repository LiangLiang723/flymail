"""Thread list, detail, and local body response schemas."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _ImmutableModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ThreadListItem(_ImmutableModel):
    id: str
    latest_message_id: str
    latest_message_at: float
    subject: str
    participants_summary: str
    latest_snippet: str
    message_count: int = Field(ge=0)
    unread_count: int = Field(ge=0)
    is_starred: bool
    has_attachments: bool
    account_count: int = Field(ge=0)
    account_ids: tuple[str, ...]
    pending_operation_count: int = Field(ge=0)
    projection_version: int = Field(ge=1)


class ThreadListResponse(_ImmutableModel):
    items: tuple[ThreadListItem, ...]
    next_cursor: str | None


class ThreadAttachment(_ImmutableModel):
    id: str
    filename: str
    content_type: str
    disposition: str
    remote_size_bytes: int = Field(ge=0)
    is_inline: bool
    cache_state: str


class ThreadOperation(_ImmutableModel):
    id: str
    operation_type: str
    status: str
    account_id: str | None
    remote_instance_id: str | None
    created_at: float
    updated_at: float


class ThreadMembership(_ImmutableModel):
    account_id: str
    mailbox_id: str
    native_name: str
    semantic_key: str
    membership_kind: str
    provider_label: str


class ThreadMessage(_ImmutableModel):
    id: str
    subject: str
    from_addresses: tuple[str, ...]
    to_addresses: tuple[str, ...]
    cc_addresses: tuple[str, ...]
    reply_to_addresses: tuple[str, ...]
    sent_at: float
    received_at: float
    size_bytes: int = Field(ge=0)
    has_attachments: bool
    snippet: str
    body_state: str
    search_state: str
    source_account_ids: tuple[str, ...]
    memberships: tuple[ThreadMembership, ...]
    attachments: tuple[ThreadAttachment, ...]
    operations: tuple[ThreadOperation, ...]


class ThreadDetailResponse(_ImmutableModel):
    id: str
    normalized_subject: str
    created_at: float
    updated_at: float
    messages: tuple[ThreadMessage, ...]


class BodyQueuedResponse(_ImmutableModel):
    message_id: str
    state: str
    job_id: str
