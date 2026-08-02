"""Notification center and safe preference schemas."""

from __future__ import annotations

import re
from typing import Literal

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


NotificationChannelKey = Literal[
    "in_app", "bark", "telegram", "wecom", "dingtalk", "feishu", "generic_webhook"
]


class NotificationChannelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel_key: NotificationChannelKey
    display_name: str = Field(min_length=1, max_length=191)
    enabled: bool = True
    public_config: dict[str, str | int | bool] = Field(default_factory=dict)
    secret: dict[str, str] = Field(default_factory=dict)
    use_proxy: bool = False


class NotificationChannelResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    channel_key: NotificationChannelKey
    display_name: str
    enabled: bool
    public_config: dict[str, str | int | bool]
    secret_configured: bool
    use_proxy: bool
    updated_at: float


class NotificationChannelListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[NotificationChannelResponse, ...]


class NotificationRuleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: Literal["mail.new", "send.failed", "backup.completed", "sync.failed"]
    channel_id: str = Field(min_length=1, max_length=64)
    image_publisher_id: str | None = Field(default=None, max_length=64)
    enabled: bool = True
    use_proxy: bool = False
    dedupe_window_seconds: int = Field(default=0, ge=0, le=86400)


class NotificationRuleResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    event_type: str
    channel_id: str
    image_publisher_id: str | None
    enabled: bool
    use_proxy: bool
    dedupe_window_seconds: int
    updated_at: float


class NotificationPublisherRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    publisher_key: Literal["flymail_imgbed", "generic_https"]
    display_name: str = Field(min_length=1, max_length=191)
    endpoint_url: str = Field(min_length=8, max_length=2048)
    enabled: bool = True
    public_config: dict[str, str | int | bool] = Field(default_factory=dict)
    secret: dict[str, str] = Field(default_factory=dict)


class NotificationPublisherResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    publisher_key: Literal["flymail_imgbed", "generic_https"]
    display_name: str
    endpoint_url: str
    enabled: bool
    public_config: dict[str, str | int | bool]
    secret_configured: bool
    updated_at: float


class NotificationTestResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
    status: Literal["pending"] = "pending"
