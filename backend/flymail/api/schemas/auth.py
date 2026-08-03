"""Authentication and administrator API schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from flymail.repositories.users import User


class UserResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    username: str
    role: Literal["admin", "user"]
    enabled: bool
    password_version: int

    @classmethod
    def from_user(cls, user: User) -> "UserResponse":
        return cls(
            id=user.id,
            username=user.username,
            role=user.role,
            enabled=user.enabled,
            password_version=user.password_version,
        )


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=191)
    password: str = Field(min_length=1, repr=False)


class AuthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user: UserResponse
    csrf_token: str


class PasswordChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, repr=False)
    new_password: str = Field(min_length=1, repr=False)
    revoke_other_sessions: bool = True


class OkResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool = True


class CreateUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=191)
    password: str = Field(min_length=1, repr=False)
    role: Literal["admin", "user"] = "user"
    enabled: bool = True


class ResetPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_password: str = Field(min_length=1, repr=False)


class UserListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[UserResponse]


class RevokeSessionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_uid: str = Field(min_length=1, max_length=64)


class RevokeSessionsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revoked_sessions: int = Field(ge=0)
