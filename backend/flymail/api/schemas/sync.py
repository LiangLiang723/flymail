"""Tenant sync-center, conflict, and aggregate diagnostic schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SyncPhaseStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    completed: int = Field(ge=0)
    total: int = Field(ge=0)
    updated_at: float


class SyncAccountStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    account_id: str
    status: str
    idle_status: str
    last_activity_at: float
    next_reconcile_at: float
    failure_count: int = Field(ge=0)
    backoff_until: float
    phases: dict[str, SyncPhaseStatus]
    pending_operations: int = Field(ge=0)
    conflicts: int = Field(ge=0)


class SyncCenterResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    accounts: tuple[SyncAccountStatus, ...]


class SyncTaskResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
    status: Literal["pending"] = "pending"


class ConflictItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: str
    operation_type: str
    target_type: str
    target_id: str
    account_id: str | None
    status: str
    error_class: str
    error_message: str
    created_at: float
    updated_at: float


class ConflictListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[ConflictItem, ...]


class ConflictResolutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["retry_operation", "cancel_operation"]
    mailbox_id: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_action_fields(self) -> "ConflictResolutionRequest":
        if self.action == "cancel_operation" and self.mailbox_id is not None:
            raise ValueError("mailbox_id is only valid for operation retry")
        return self


class ConflictResolutionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: str
    status: Literal["pending", "cancelled"]
    task_id: str | None = None


class AdminDiagnosticsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    users: int = Field(ge=0)
    accounts: int = Field(ge=0)
    runnable_jobs: int = Field(ge=0)
    failed_jobs: int = Field(ge=0)
    conflicts: int = Field(ge=0)
    worker_heartbeat_at: float | None
