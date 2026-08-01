"""Stable provider capability and plugin contracts for FlyMail V2."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Protocol, Sequence, runtime_checkable

if TYPE_CHECKING:
    from flymail.providers.errors import ProviderError


class TransportSecurity(str, Enum):
    TLS = "tls"
    STARTTLS = "starttls"


class SentCopyStrategy(str, Enum):
    PROVIDER_AUTO = "provider_auto"
    IMAP_APPEND = "imap_append"


@dataclass(frozen=True, slots=True)
class ServiceEndpoint:
    host: str
    port: int
    security: TransportSecurity

    def __post_init__(self) -> None:
        host = str(self.host or "").strip().casefold()
        if not host or any(character.isspace() for character in host):
            raise ValueError("endpoint host is required")
        if isinstance(self.port, bool):
            raise TypeError("endpoint port must be an integer")
        port = int(self.port)
        if not 1 <= port <= 65535:
            raise ValueError("endpoint port must be between 1 and 65535")
        if not isinstance(self.security, TransportSecurity):
            raise TypeError("endpoint security must be TransportSecurity")
        object.__setattr__(self, "host", host)
        object.__setattr__(self, "port", port)


@dataclass(frozen=True, slots=True)
class EndpointVariant:
    domain_suffixes: tuple[str, ...]
    imap: ServiceEndpoint
    smtp: ServiceEndpoint

    def __post_init__(self) -> None:
        normalized = tuple(
            str(suffix).strip().casefold()
            for suffix in self.domain_suffixes
            if str(suffix or "").strip()
        )
        if not normalized or any(not suffix.startswith("@") for suffix in normalized):
            raise ValueError("endpoint variant domain suffixes must start with @")
        if len(set(normalized)) != len(normalized):
            raise ValueError("endpoint variant domain suffixes must be unique")
        object.__setattr__(self, "domain_suffixes", normalized)


@dataclass(frozen=True, slots=True)
class ProviderEndpoints:
    imap: ServiceEndpoint | None
    smtp: ServiceEndpoint | None
    variants: tuple[EndpointVariant, ...] = ()
    user_supplied: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.user_supplied, bool):
            raise TypeError("user_supplied must be bool")
        if not self.user_supplied and (self.imap is None or self.smtp is None):
            raise ValueError("fixed providers require IMAP and SMTP endpoints")
        if self.user_supplied and bool(self.imap is None) != bool(self.smtp is None):
            raise ValueError("user-supplied endpoints must provide both protocols or neither")
        variants = tuple(self.variants)
        if any(not isinstance(variant, EndpointVariant) for variant in variants):
            raise TypeError("endpoint variants must be EndpointVariant values")
        suffixes = [suffix for variant in variants for suffix in variant.domain_suffixes]
        if len(set(suffixes)) != len(suffixes):
            raise ValueError("endpoint variant domains must not overlap")
        object.__setattr__(self, "variants", variants)


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    supports_idle: bool
    supports_move: bool
    supports_uidplus: bool
    supports_condstore: bool
    supports_qresync: bool
    supports_gmail_labels: bool
    supports_special_use: bool
    supports_smtp_utf8: bool
    supports_oauth: bool
    auto_saves_sent_copy: bool
    max_parallel_connections: int
    recommended_poll_seconds: int
    idle_refresh_seconds: int
    max_fetch_batch: int
    max_attachment_bytes: int

    def __post_init__(self) -> None:
        boolean_fields = (
            "supports_idle",
            "supports_move",
            "supports_uidplus",
            "supports_condstore",
            "supports_qresync",
            "supports_gmail_labels",
            "supports_special_use",
            "supports_smtp_utf8",
            "supports_oauth",
            "auto_saves_sent_copy",
        )
        for field_name in boolean_fields:
            if not isinstance(getattr(self, field_name), bool):
                raise TypeError(f"{field_name} must be bool")
        integer_minimums = {
            "max_parallel_connections": 1,
            "recommended_poll_seconds": 60,
            "idle_refresh_seconds": 60,
            "max_fetch_batch": 1,
            "max_attachment_bytes": 1,
        }
        for field_name, minimum in integer_minimums.items():
            raw_value = getattr(self, field_name)
            if isinstance(raw_value, bool):
                raise TypeError(f"{field_name} must be an integer")
            value = int(raw_value)
            if value < minimum:
                raise ValueError(f"{field_name} must be at least {minimum}")
            object.__setattr__(self, field_name, value)


@dataclass(frozen=True, slots=True)
class MailboxMapping:
    native_key: str
    semantic_key: str
    attributes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        native_key = str(self.native_key or "")
        if not native_key.strip():
            raise ValueError("native mailbox key is required")
        semantic_key = str(self.semantic_key or "").strip().casefold()
        allowed = {
            "inbox",
            "sent",
            "drafts",
            "junk",
            "trash",
            "archive",
            "all_mail",
            "important",
            "custom",
        }
        if semantic_key not in allowed:
            raise ValueError(f"unsupported semantic mailbox key: {semantic_key}")
        attributes = tuple(
            sorted(
                {
                    str(attribute or "").strip().casefold()
                    for attribute in self.attributes
                    if str(attribute or "").strip()
                }
            )
        )
        object.__setattr__(self, "native_key", native_key)
        object.__setattr__(self, "semantic_key", semantic_key)
        object.__setattr__(self, "attributes", attributes)


@runtime_checkable
class ProviderPlugin(Protocol):
    key: str

    def capabilities(self) -> ProviderCapabilities: ...

    def default_endpoints(self) -> ProviderEndpoints: ...

    def map_mailbox(self, native_key: str, attributes: set[str]) -> MailboxMapping: ...

    def classify_error(self, operation: str, response: object) -> "ProviderError": ...

    def sent_copy_strategy(self) -> SentCopyStrategy: ...

    def normalize_labels(self, raw_labels: Sequence[str]) -> tuple[str, ...]: ...
