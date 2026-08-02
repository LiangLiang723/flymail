"""Notification center and safe preference schemas."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator


class NotificationItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    event_type: str
    title: str
    summary: str
    action_path: str
    account_id: str | None
    created_at: float
    read: bool


class NotificationListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[NotificationItem, ...]
    next_cursor: str | None
    unread_count: int = Field(ge=0)


class NotificationReadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    read: bool


class NotificationReadAllResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    updated_count: int = Field(ge=0)


class QuietHours(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    start: str
    end: str

    @field_validator("start", "end")
    @classmethod
    def validate_time(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", normalized):
            raise ValueError("time must use 24-hour HH:MM format")
        return normalized


class NotificationSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    in_app_enabled: bool = True
    external_enabled: bool = True
    include_images: bool = False
    quiet_hours: QuietHours | None = None
    event_preferences: dict[str, bool] = Field(default_factory=dict)

    @field_validator("event_preferences")
    @classmethod
    def validate_events(cls, value: dict[str, bool]) -> dict[str, bool]:
        allowed = {"new_mail", "send_failed", "sync_failed", "auth_required"}
        if not set(value).issubset(allowed):
            raise ValueError("unsupported notification event preference")
        return dict(value)


class NotificationSettingsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    in_app_enabled: bool
    external_enabled: bool
    include_images: bool
    quiet_hours: QuietHours | None
    event_preferences: dict[str, bool]
    updated_at: float
