"""Provider-neutral error categories and credential-safe classification."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from enum import Enum
from types import MappingProxyType
from typing import Any


class ProviderErrorCode(str, Enum):
    AUTHENTICATION_FAILED = "authentication_failed"
    AUTHORIZATION_REQUIRED = "authorization_required"
    CONNECTION_FAILED = "connection_failed"
    RATE_LIMITED = "rate_limited"
    MAILBOX_NOT_FOUND = "mailbox_not_found"
    MESSAGE_NOT_FOUND = "message_not_found"
    MESSAGE_TOO_LARGE = "message_too_large"
    UNSUPPORTED_OPERATION = "unsupported_operation"
    SERVER_REJECTED = "server_rejected"
    TEMPORARY_SERVER_ERROR = "temporary_server_error"
    PROTOCOL_ERROR = "protocol_error"


_SAFE_DETAILS = {
    ProviderErrorCode.AUTHENTICATION_FAILED: "Mailbox authentication failed",
    ProviderErrorCode.AUTHORIZATION_REQUIRED: "Mailbox authorization must be renewed",
    ProviderErrorCode.CONNECTION_FAILED: "Mailbox server connection failed",
    ProviderErrorCode.RATE_LIMITED: "Mailbox provider rate limit reached",
    ProviderErrorCode.MAILBOX_NOT_FOUND: "Remote mailbox was not found",
    ProviderErrorCode.MESSAGE_NOT_FOUND: "Remote message was not found",
    ProviderErrorCode.MESSAGE_TOO_LARGE: "Message exceeds the provider size limit",
    ProviderErrorCode.UNSUPPORTED_OPERATION: "Mailbox provider does not support this operation",
    ProviderErrorCode.SERVER_REJECTED: "Mailbox provider rejected the request",
    ProviderErrorCode.TEMPORARY_SERVER_ERROR: "Mailbox provider reported a temporary error",
    ProviderErrorCode.PROTOCOL_ERROR: "Mailbox provider returned an invalid protocol response",
}

_RETRYABLE_CODES = {
    ProviderErrorCode.CONNECTION_FAILED,
    ProviderErrorCode.RATE_LIMITED,
    ProviderErrorCode.TEMPORARY_SERVER_ERROR,
}

_SENSITIVE_KEY_FRAGMENTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "authorization",
    "cookie",
    "credential",
    "api_key",
    "apikey",
    "access_key",
)

_ASSIGNMENT_SECRET_PATTERN = re.compile(
    r"(?i)\b(password|passwd|secret|token|authorization|client_secret|refresh_token|access_token)"
    r"\s*[:=]\s*(?:bearer\s+)?[^\s,;\]}]+"
)
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+\-/=]+")


def _is_sensitive_key(value: str) -> bool:
    normalized = value.strip().casefold().replace("-", "_")
    return any(fragment in normalized for fragment in _SENSITIVE_KEY_FRAGMENTS)


def _redact_text(value: str) -> str:
    redacted = _ASSIGNMENT_SECRET_PATTERN.sub(lambda match: f"{match.group(1)}=***", value)
    return _BEARER_PATTERN.sub("Bearer ***", redacted)


def redact_debug_value(value: Any):
    if isinstance(value, Mapping):
        return {
            str(key): "***" if _is_sensitive_key(str(key)) else redact_debug_value(child)
            for key, child in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(redact_debug_value(child) for child in value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<{type(value).__name__}:{len(value)} bytes>"
    if isinstance(value, BaseException):
        return {
            "type": type(value).__name__,
            "message": _redact_text(str(value))[:1024],
        }
    if isinstance(value, str):
        return _redact_text(value)[:2048]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return f"<{type(value).__name__}>"


class ProviderError(Exception):
    """Safe provider error surfaced to application and Worker layers."""

    __slots__ = ("code", "retryable", "safe_detail", "debug_context")

    def __init__(
        self,
        code: ProviderErrorCode,
        *,
        retryable: bool | None = None,
        safe_detail: str | None = None,
        debug_context: Mapping[str, Any] | None = None,
    ) -> None:
        if not isinstance(code, ProviderErrorCode):
            raise TypeError("code must be ProviderErrorCode")
        detail = str(safe_detail or _SAFE_DETAILS[code]).strip()
        if not detail:
            raise ValueError("safe_detail is required")
        self.code = code
        self.retryable = code in _RETRYABLE_CODES if retryable is None else bool(retryable)
        self.safe_detail = detail
        safe_context = redact_debug_value(dict(debug_context or {}))
        self.debug_context = MappingProxyType(safe_context)
        super().__init__(detail)

    def __str__(self) -> str:
        return self.safe_detail

    def __repr__(self) -> str:
        return (
            "ProviderError("
            f"code={self.code.value!r}, retryable={self.retryable!r}, "
            f"safe_detail={self.safe_detail!r})"
        )


def _response_status(response: object) -> int | None:
    candidates: list[object] = []
    if isinstance(response, Mapping):
        candidates.extend(response.get(key) for key in ("status", "status_code", "code"))
    else:
        candidates.extend(getattr(response, key, None) for key in ("status", "status_code", "code"))
    for candidate in candidates:
        if isinstance(candidate, int):
            return candidate
        if isinstance(candidate, str) and candidate.strip().isdigit():
            return int(candidate.strip())
    return None


def _flatten_text(value: object) -> str:
    parts: list[str] = []

    def visit(item: object) -> None:
        if isinstance(item, Mapping):
            for key, child in item.items():
                if not _is_sensitive_key(str(key)):
                    parts.append(str(key))
                    visit(child)
            return
        if isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray, memoryview)):
            for child in item:
                visit(child)
            return
        if isinstance(item, BaseException):
            parts.append(type(item).__name__)
            parts.append(str(item))
            return
        if isinstance(item, (bytes, bytearray, memoryview)):
            parts.append(bytes(item[:1024]).decode("utf-8", errors="replace"))
            return
        if item is not None:
            parts.append(str(item))

    visit(value)
    return " ".join(parts).casefold()


def classify_provider_error(
    operation: str,
    response: object,
    *,
    provider_key: str = "generic",
) -> ProviderError:
    normalized_operation = str(operation or "").strip().casefold()
    status = _response_status(response)
    text = _flatten_text(response)
    combined = f"{normalized_operation} {text}"

    if (
        "invalid_grant" in combined
        or "consent_required" in combined
        or "interaction_required" in combined
        or "authorization required" in combined
        or ("oauth" in normalized_operation and "expired" in combined)
    ):
        code = ProviderErrorCode.AUTHORIZATION_REQUIRED
    elif (
        status == 429
        or any(term in combined for term in ("rate limit", "rate_limited", "too many requests", "throttl"))
    ):
        code = ProviderErrorCode.RATE_LIMITED
    elif isinstance(response, (TimeoutError, ConnectionError, OSError)) or any(
        term in combined
        for term in (
            "connection refused",
            "connection reset",
            "connection failed",
            "network unreachable",
            "timed out",
            "timeout",
            "disconnected",
            "broken pipe",
        )
    ):
        code = ProviderErrorCode.CONNECTION_FAILED
    elif (
        status in {401, 535}
        or any(
            term in combined
            for term in (
                "authenticationfailed",
                "authentication failed",
                "invalid credentials",
                "login failed",
                "username and password not accepted",
            )
        )
    ):
        code = ProviderErrorCode.AUTHENTICATION_FAILED
    elif any(
        term in combined
        for term in (
            "mailbox does not exist",
            "mailbox not found",
            "no such mailbox",
            "unknown mailbox",
        )
    ):
        code = ProviderErrorCode.MAILBOX_NOT_FOUND
    elif status == 552 or any(
        term in combined
        for term in (
            "message too large",
            "message size exceeds",
            "maximum message size",
            "size limit exceeded",
        )
    ):
        code = ProviderErrorCode.MESSAGE_TOO_LARGE
    elif any(
        term in combined
        for term in (
            "message not found",
            "no such message",
            "unknown message",
            "uid not found",
        )
    ):
        code = ProviderErrorCode.MESSAGE_NOT_FOUND
    elif any(
        term in combined
        for term in (
            "not supported",
            "unsupported command",
            "command unsupported",
            "unknown command",
        )
    ):
        code = ProviderErrorCode.UNSUPPORTED_OPERATION
    elif status in {421, 425, 450, 451, 452, 454} or any(
        term in combined
        for term in (
            "temporary local problem",
            "temporary server error",
            "temporarily unavailable",
            "try again later",
        )
    ):
        code = ProviderErrorCode.TEMPORARY_SERVER_ERROR
    elif (
        (status is not None and 500 <= status <= 599)
        or any(
            term in combined
            for term in ("recipient rejected", "server rejected", "request rejected", "denied")
        )
    ):
        code = ProviderErrorCode.SERVER_REJECTED
    else:
        code = ProviderErrorCode.PROTOCOL_ERROR

    return ProviderError(
        code,
        debug_context={
            "provider": str(provider_key or "generic").strip().casefold(),
            "operation": normalized_operation,
            "response_status": status,
            "response": response,
        },
    )
