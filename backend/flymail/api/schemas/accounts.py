"""Mailbox account request and public response schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from flymail.repositories.accounts import MailAccount, MailIdentity
from flymail.repositories.base import normalize_email


def _validated_email(value: str, *, allow_blank: bool = False) -> str:
    normalized = str(value or "").strip()
    if allow_blank and not normalized:
        return ""
    try:
        normalize_email(normalized)
    except ValueError:
        raise ValueError("invalid email address") from None
    return normalized


class ServiceEndpointRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    security: Literal["tls", "starttls"]


class EndpointConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    imap: ServiceEndpointRequest
    smtp: ServiceEndpointRequest


class CreateAccountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_key: str = Field(min_length=1, max_length=64)
    email: str = Field(min_length=3, max_length=320)
    display_name: str = Field(default="", max_length=191)
    credential_type: Literal["password", "authorization_code"]
    credential: str = Field(min_length=1, max_length=8192)
    endpoint_config: EndpointConfigRequest | None = None
    poll_interval_seconds: int = Field(default=300, ge=5, le=3600)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return _validated_email(value)


class UpdateAccountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, max_length=191)
    remark: str | None = Field(default=None, max_length=255)
    group_name: str | None = Field(default=None, max_length=191)
    poll_interval_seconds: int | None = Field(default=None, ge=5, le=3600)
    enabled: bool | None = None


class AccountResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    provider_key: str
    email: str
    display_name: str
    remark: str
    group_name: str
    status: str
    endpoint_config: dict
    icon_mode: str
    icon_value: str
    icon_object_sha256: str | None
    poll_interval_seconds: int
    created_at: float
    updated_at: float

    @classmethod
    def from_account(cls, account: MailAccount) -> "AccountResponse":
        return cls(
            id=account.id,
            provider_key=account.provider_key,
            email=account.email,
            display_name=account.display_name,
            remark=account.remark,
            group_name=account.group_name,
            status=account.status,
            endpoint_config=dict(account.endpoint_config),
            icon_mode=account.icon_mode,
            icon_value=account.icon_value,
            icon_object_sha256=account.icon_object_sha256,
            poll_interval_seconds=account.poll_interval_seconds,
            created_at=account.created_at,
            updated_at=account.updated_at,
        )


class OAuthStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_key: Literal["gmail", "outlook"]
    email: str = Field(min_length=3, max_length=320)
    display_name: str = Field(default="", max_length=191)
    redirect_uri: str = Field(min_length=8, max_length=2048)
    account_id: str | None = Field(default=None, max_length=64)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return _validated_email(value)


class OAuthStartResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: str
    account_id: str
    authorization_url: str
    expires_at: float


class OAuthStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["pending", "consumed", "expired"]


class OAuthCallbackResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account: "AccountResponse"
    job_id: str


class CreateIdentityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_address: str = Field(min_length=3, max_length=320)
    display_name: str = Field(default="", max_length=191)
    reply_to: str = Field(default="", max_length=320)
    signature_html: str = Field(default="", max_length=262144)
    signature_text: str = Field(default="", max_length=262144)
    is_default: bool = False

    @field_validator("from_address")
    @classmethod
    def validate_from_address(cls, value: str) -> str:
        return _validated_email(value)

    @field_validator("reply_to")
    @classmethod
    def validate_reply_to(cls, value: str) -> str:
        return _validated_email(value, allow_blank=True)


class UpdateIdentityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, max_length=191)
    reply_to: str | None = Field(default=None, max_length=320)
    signature_html: str | None = Field(default=None, max_length=262144)
    signature_text: str | None = Field(default=None, max_length=262144)
    is_default: bool | None = None

    @field_validator("reply_to")
    @classmethod
    def validate_reply_to(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validated_email(value, allow_blank=True)


class IdentityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    account_id: str
    from_address: str
    display_name: str
    reply_to: str
    signature_html: str
    signature_text: str
    is_default: bool
    is_verified: bool

    @classmethod
    def from_identity(cls, identity: MailIdentity) -> "IdentityResponse":
        return cls(
            id=identity.id,
            account_id=identity.account_id,
            from_address=identity.from_address,
            display_name=identity.display_name,
            reply_to=identity.reply_to,
            signature_html=identity.signature_html,
            signature_text=identity.signature_text,
            is_default=identity.is_default,
            is_verified=identity.is_verified,
        )


class IdentityListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[IdentityResponse]


class UpdateCredentialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    credential_type: Literal["password", "authorization_code"]
    credential: str = Field(min_length=1, max_length=8192)


class DeleteAccountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm_email: str = Field(min_length=3, max_length=320)

    @field_validator("confirm_email")
    @classmethod
    def validate_confirm_email(cls, value: str) -> str:
        return _validated_email(value)


class DeleteAccountResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account: AccountResponse
    cleanup_job_id: str


class SaveProxyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scheme: Literal["http"] = "http"
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    username: str = Field(default="", max_length=255)
    password: str = Field(default="", max_length=4096)

    @model_validator(mode="after")
    def validate_credentials(self) -> "SaveProxyRequest":
        if self.password and not self.username:
            raise ValueError("proxy username is required when password is supplied")
        return self


class ProxyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    scheme: str
    host: str
    port: int
    enabled: bool
    has_credentials: bool


class VerificationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    status_url: str


class AccountListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AccountResponse]
