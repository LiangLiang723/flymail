"""Local-first mail operation API schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


OperationType = Literal[
    "set_read",
    "set_starred",
    "add_label",
    "remove_label",
    "move",
    "archive",
    "trash",
    "delete_permanent",
]


class OperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_type: Literal["remote_instance", "thread"]
    target_id: str = Field(min_length=1, max_length=64)
    operation_type: OperationType
    desired_state: dict = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=1, max_length=191)
    confirmation_token: str | None = Field(default=None, max_length=4096)


class OperationInitialStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: str
    status: Literal["pending"] = "pending"


class OperationAcceptedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_group_id: str | None = None
    operation_ids: tuple[str, ...]
    items: tuple[OperationInitialStatus, ...]
    status: Literal["pending"] = "pending"


class PermanentDeleteConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_type: Literal["remote_instance", "thread"]
    target_id: str = Field(min_length=1, max_length=64)


class PermanentDeleteConfirmationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    confirmation_token: str
    expires_at: float


class UndoOperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=1, max_length=191)


class UndoOperationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: str
    status: Literal["cancelled", "pending"]


class MarkAllReadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    semantic_mailbox: str = Field(default="inbox", min_length=1, max_length=64)
    account_id: str | None = Field(default=None, max_length=64)
    native_label: str | None = Field(default=None, max_length=64)
    idempotency_key: str = Field(min_length=1, max_length=191)


class MarkAllReadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    bulk_operation_id: str
    job_id: str
    status: Literal["pending"] = "pending"
