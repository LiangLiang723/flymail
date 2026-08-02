"""Attachment and raw-message content response schemas."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class AttachmentMetadataResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    message_id: str
    filename: str
    content_type: str
    disposition: str
    remote_size_bytes: int = Field(ge=0)
    is_inline: bool
    cache_state: str


class ContentQueuedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    resource_id: str
    state: str
    job_id: str


class RawMessageStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    message_id: str
    state: str
    size_bytes: int = Field(ge=0)
