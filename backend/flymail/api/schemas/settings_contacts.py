"""User settings, contacts, and administrator history-sync schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from flymail.repositories.base import normalize_email


MEBIBYTE = 1024 * 1024
MIN_CACHE_QUOTA_BYTES = 100 * MEBIBYTE


class UiPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    theme: Literal["system", "light", "dark"] = "system"
    density: Literal["comfortable", "compact"] = "comfortable"


class ComposePreferences(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    autosave_seconds: int = Field(default=10, ge=3, le=300)


class RemoteImagePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    default: Literal["block", "allow"] = "block"


class SettingsUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body_cache_quota_bytes: int | None = Field(default=None, ge=0)
    attachment_cache_quota_bytes: int | None = Field(default=None, ge=0)
    ui_preferences: UiPreferences | None = None
    compose_preferences: ComposePreferences | None = None
    remote_image_policy: RemoteImagePolicy | None = None

    @model_validator(mode="after")
    def require_change(self) -> "SettingsUpdateRequest":
        if all(
            value is None
            for value in (
                self.body_cache_quota_bytes,
                self.attachment_cache_quota_bytes,
                self.ui_preferences,
                self.compose_preferences,
                self.remote_image_policy,
            )
        ):
            raise ValueError("at least one setting must be supplied")
        for label, value in (
            ("body cache quota", self.body_cache_quota_bytes),
            ("attachment cache quota", self.attachment_cache_quota_bytes),
        ):
            if value is not None and value != 0 and value < MIN_CACHE_QUOTA_BYTES:
                raise ValueError(f"{label} must be 0 or at least 100 MiB")
        return self


class SettingsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    body_cache_quota_bytes: int = Field(ge=0)
    attachment_cache_quota_bytes: int = Field(ge=0)
    ui_preferences: UiPreferences
    compose_preferences: ComposePreferences
    remote_image_policy: RemoteImagePolicy
    body_cache_usage_bytes: int = Field(ge=0)
    attachment_cache_usage_bytes: int = Field(ge=0)
    cleanup_task_id: str | None = None
    updated_at: float


class ContactCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(default="", max_length=191)
    primary_email: str = Field(min_length=3, max_length=320)
    emails: tuple[str, ...] = Field(default=(), max_length=20)

    @field_validator("primary_email")
    @classmethod
    def validate_primary_email(cls, value: str) -> str:
        display = str(value or "").strip()
        normalize_email(display)
        return display

    @field_validator("emails")
    @classmethod
    def validate_emails(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            display = str(value or "").strip()
            normalized = normalize_email(display)
            if normalized in seen:
                continue
            seen.add(normalized)
            result.append(display)
        return tuple(result)


class ContactUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, max_length=191)
    primary_email: str | None = Field(default=None, min_length=3, max_length=320)
    emails: tuple[str, ...] | None = Field(default=None, max_length=20)

    @field_validator("primary_email")
    @classmethod
    def validate_primary_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        display = str(value or "").strip()
        normalize_email(display)
        return display

    @field_validator("emails")
    @classmethod
    def validate_emails(cls, values: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if values is None:
            return None
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            display = str(value or "").strip()
            normalized = normalize_email(display)
            if normalized in seen:
                continue
            seen.add(normalized)
            result.append(display)
        return tuple(result)

    @model_validator(mode="after")
    def require_change(self) -> "ContactUpdateRequest":
        if self.display_name is None and self.primary_email is None and self.emails is None:
            raise ValueError("at least one contact field must be supplied")
        return self


class ContactResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    display_name: str
    primary_email: str
    emails: tuple[str, ...]
    created_at: float
    updated_at: float


class ContactListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[ContactResponse, ...]


class SyncCursorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    phase: str
    cursor_type: str
    cursor: dict
    last_uid: int = Field(ge=0)
    highest_modseq: int = Field(ge=0)
    updated_at: float


class HistorySyncItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: str
    user_uid: str
    account_id: str | None
    account_email: str
    provider_key: str | None
    status: str
    queue_name: str
    attempt_count: int = Field(ge=0)
    max_attempts: int = Field(ge=1)
    available_at: float
    last_error_class: str
    last_error_message: str
    created_at: float
    updated_at: float
    cursor: SyncCursorResponse | None


class HistorySyncListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[HistorySyncItem, ...]


class HistorySyncActionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: str
    status: Literal["paused", "pending"]
