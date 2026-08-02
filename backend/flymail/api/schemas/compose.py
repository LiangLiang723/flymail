"""Versioned draft, attachment, template, and reliable-send schemas."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from flymail.repositories.base import normalize_email


class Recipient(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    address: str = Field(min_length=3, max_length=320)
    display_name: str = Field(default="", max_length=191)

    @field_validator("address")
    @classmethod
    def valid_address(cls, value: str) -> str:
        display = str(value or "").strip()
        normalize_email(display)
        return display


class RecipientGroups(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    to: tuple[Recipient, ...] = ()
    cc: tuple[Recipient, ...] = ()
    bcc: tuple[Recipient, ...] = ()


class DraftCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(min_length=1, max_length=64)
    identity_id: str = Field(min_length=1, max_length=64)
    thread_id: str | None = Field(default=None, max_length=64)
    reply_to_message_id: str | None = Field(default=None, max_length=64)
    subject: str = Field(default="", max_length=998)
    body_html: str = Field(default="", max_length=2_097_152)
    body_text: str = Field(default="", max_length=2_097_152)
    recipients: RecipientGroups
    scheduled_at: float | None = Field(default=None, ge=0)


class DraftUpdateRequest(DraftCreateRequest):
    expected_version: int = Field(ge=1)


class DraftAttachmentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    filename: str
    content_type: str
    size_bytes: int = Field(ge=0)
    position_index: int = Field(ge=0)
    created_at: float


class DraftResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    account_id: str
    identity_id: str
    thread_id: str | None
    reply_to_message_id: str | None
    subject: str
    body_html: str
    body_text: str
    recipients: RecipientGroups
    attachments: tuple[DraftAttachmentResponse, ...]
    version: int
    status: str
    send_state: str
    scheduled_at: float | None
    send_message_id: str
    created_at: float
    updated_at: float
    queued_at: float | None
    sent_at: float | None


class ComposeTemplateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    account_id: str
    identity_id: str
    thread_id: str | None
    reply_to_message_id: str
    subject: str
    body_html: str
    body_text: str
    recipients: RecipientGroups


class StorageRootResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    label: str
    visibility_scope: str


class StorageRootListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[StorageRootResponse, ...]


class ImportAttachmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root_id: str = Field(min_length=1, max_length=64)
    relative_path: str = Field(min_length=1, max_length=4096)


class SendDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=1, max_length=191)


class SendDraftResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    draft_id: str
    operation_id: str
    job_id: str
    message_id_header: str


class CancelSendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: str = Field(min_length=1, max_length=64)
