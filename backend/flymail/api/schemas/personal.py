"""Profile, account-icon, contact helper, and storage-root schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProfileUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nickname: str = Field(max_length=191)


class ProfileResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_uid: str
    username: str
    role: str
    nickname: str
    avatar_url: str | None


class AccountIconRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["provider", "preset"]
    value: str = Field(default="", max_length=64)

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: str, info):
        normalized = str(value or "").strip()
        if info.data.get("mode") == "preset" and normalized not in {
            "mail", "briefcase", "personal", "school", "star", "cloud"
        }:
            raise ValueError("unsupported account icon preset")
        if info.data.get("mode") == "provider" and normalized:
            raise ValueError("provider icon does not accept a preset value")
        return normalized


class AccountIconResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    account_id: str
    mode: Literal["provider", "preset", "uploaded"]
    value: str
    content_url: str | None


class QuickAddContactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(min_length=1, max_length=64)


class StorageRootCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=191)
    path: str = Field(min_length=1, max_length=1024)
    visibility_scope: Literal["all", "user"] = "all"
    user_uid: str | None = Field(default=None, max_length=64)


class StorageRootResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    label: str
    visibility_scope: Literal["all", "user"]
    user_uid: str | None


class StorageRootListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[StorageRootResponse, ...]


class StorageEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    relative_path: str
    entry_type: Literal["file", "directory"]
    size_bytes: int = Field(ge=0)


class StorageBrowseResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    root_id: str
    path: str
    items: tuple[StorageEntry, ...]
