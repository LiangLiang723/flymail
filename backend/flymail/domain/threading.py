"""Canonical message identity and conservative user-level thread resolution."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Iterable


_REPLY_PREFIX = re.compile(
    r"^\s*(?:(?:re|fw|fwd)\s*(?:\[[0-9]+\])?\s*:\s*)+",
    re.IGNORECASE,
)
_WHITESPACE = re.compile(r"\s+")


def _digest(*parts: object) -> str:
    payload = "\x1f".join(str(part or "") for part in parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def normalize_message_id(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    normalized = normalized.strip("<>").strip().casefold()
    if not normalized or any(character.isspace() for character in normalized):
        return ""
    return f"<{normalized}>"


def normalize_subject(value: str) -> str:
    subject = str(value or "").strip()
    previous = None
    while subject != previous:
        previous = subject
        subject = _REPLY_PREFIX.sub("", subject).strip()
    return _WHITESPACE.sub(" ", subject).casefold()


def normalize_address(value: str) -> str:
    normalized = str(value or "").strip().casefold()
    return normalized if "@" in normalized and not any(character.isspace() for character in normalized) else ""


def normalized_participants(*groups: Iterable[str]) -> frozenset[str]:
    return frozenset(
        normalized
        for group in groups
        for value in group
        if (normalized := normalize_address(value))
    )


def canonical_message_key(
    *,
    user_uid: str,
    account_id: str,
    mailbox_id: str,
    uidvalidity: int,
    remote_uid: int,
    received_at: float,
    size_bytes: int,
    sender: str,
    message_id_header: str = "",
    provider_message_id: str = "",
) -> str:
    provider_id = str(provider_message_id or "").strip()
    if provider_id:
        return f"provider:{_digest(account_id, provider_id)}"
    message_id = normalize_message_id(message_id_header)
    if message_id:
        return f"message:{_digest(user_uid, message_id)}"
    return "fallback:" + _digest(
        account_id,
        mailbox_id,
        int(uidvalidity),
        int(remote_uid),
        float(received_at),
        int(size_bytes),
        normalize_address(sender),
    )


@dataclass(frozen=True, slots=True)
class ThreadHeaders:
    message_id_header: str = ""
    in_reply_to: str = ""
    references: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        message_id = normalize_message_id(self.message_id_header)
        in_reply_to = normalize_message_id(self.in_reply_to)
        references: list[str] = []
        seen: set[str] = set()
        for raw_reference in self.references:
            normalized = normalize_message_id(raw_reference)
            if normalized and normalized not in seen:
                seen.add(normalized)
                references.append(normalized)
        object.__setattr__(self, "message_id_header", message_id)
        object.__setattr__(self, "in_reply_to", in_reply_to)
        object.__setattr__(self, "references", tuple(references))


@dataclass(frozen=True, slots=True)
class ThreadDecision:
    canonical_thread_key: str
    parent_message_id_header: str
    relation_source: str
    reason_code: str


class ThreadResolver:
    """Resolve deterministic header threads without accessing persistence."""

    def resolve(self, user_uid: str, headers: ThreadHeaders) -> ThreadDecision | None:
        user = str(user_uid or "").strip()
        if not user:
            raise ValueError("user_uid is required")
        if not isinstance(headers, ThreadHeaders):
            raise TypeError("headers must be ThreadHeaders")

        if headers.references:
            anchor = headers.references[0]
            parent = headers.in_reply_to or headers.references[-1]
            reason = "references"
        elif headers.in_reply_to:
            anchor = headers.in_reply_to
            parent = headers.in_reply_to
            reason = "in_reply_to"
        elif headers.message_id_header:
            anchor = headers.message_id_header
            parent = ""
            reason = "message_id"
        else:
            return None

        return ThreadDecision(
            canonical_thread_key=f"header:{_digest(user, anchor)}",
            parent_message_id_header=parent,
            relation_source="headers",
            reason_code=reason,
        )

    @staticmethod
    def fallback_key(user_uid: str, canonical_message_key_value: str) -> str:
        return f"fallback:{_digest(user_uid, canonical_message_key_value)}"
