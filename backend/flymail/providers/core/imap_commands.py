"""Immutable IMAP commands, responses, and transport contracts."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from typing import Generic, Protocol, TypeVar

from flymail.providers.contracts import ServiceEndpoint


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ImapCredentials:
    username: str
    secret: str = field(repr=False)
    auth_kind: str = "password"

    def __post_init__(self) -> None:
        username = str(self.username or "").strip()
        secret = str(self.secret or "")
        auth_kind = str(self.auth_kind or "").strip().casefold()
        if not username:
            raise ValueError("IMAP username is required")
        if not secret:
            raise ValueError("IMAP secret is required")
        if auth_kind not in {"password", "oauth"}:
            raise ValueError("IMAP auth_kind must be password or oauth")
        object.__setattr__(self, "username", username)
        object.__setattr__(self, "secret", secret)
        object.__setattr__(self, "auth_kind", auth_kind)


@dataclass(frozen=True, slots=True)
class ImapResponse:
    status: str
    data: object = None
    text: str = ""

    def __post_init__(self) -> None:
        status = str(self.status or "").strip().upper()
        if status not in {"OK", "NO", "BAD", "BYE", "PREAUTH"}:
            raise ValueError(f"unsupported IMAP response status: {status}")
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "text", str(self.text or ""))


@dataclass(frozen=True, slots=True)
class SelectedMailbox:
    native_key: str
    exists: int = 0
    recent: int = 0
    uidvalidity: int = 0
    uidnext: int = 0
    highest_modseq: int = 0
    read_only: bool = False

    def __post_init__(self) -> None:
        native_key = str(self.native_key or "")
        if not native_key.strip():
            raise ValueError("selected mailbox native key is required")
        object.__setattr__(self, "native_key", native_key)
        for field_name in ("exists", "recent", "uidvalidity", "uidnext", "highest_modseq"):
            raw_value = getattr(self, field_name)
            if isinstance(raw_value, bool):
                raise TypeError(f"{field_name} must be an integer")
            value = int(raw_value or 0)
            if value < 0:
                raise ValueError(f"{field_name} must be non-negative")
            object.__setattr__(self, field_name, value)
        if not isinstance(self.read_only, bool):
            raise TypeError("read_only must be bool")


@dataclass(frozen=True, slots=True)
class IdleEvent:
    kind: str
    count: int | None = None
    sequence: int | None = None
    raw: object = field(default=None, repr=False)

    def __post_init__(self) -> None:
        kind = str(self.kind or "").strip().casefold()
        if kind not in {"exists", "expunge", "fetch", "recent", "bye", "timeout"}:
            raise ValueError(f"unsupported IDLE event kind: {kind}")
        object.__setattr__(self, "kind", kind)
        for field_name in ("count", "sequence"):
            raw_value = getattr(self, field_name)
            if raw_value is None:
                continue
            if isinstance(raw_value, bool):
                raise TypeError(f"{field_name} must be an integer")
            value = int(raw_value)
            if value < 0:
                raise ValueError(f"{field_name} must be non-negative")
            object.__setattr__(self, field_name, value)


class ImapTransport(Protocol):
    async def connect(
        self,
        credentials: ImapCredentials,
        endpoint: ServiceEndpoint,
        proxy: object | None,
    ) -> ImapResponse: ...

    async def execute(
        self,
        command_name: str,
        arguments: tuple[object, ...],
    ) -> ImapResponse: ...

    def idle(self, mailbox_native_key: str) -> AsyncIterator[IdleEvent]: ...

    async def close(self) -> None: ...


Parser = Callable[[ImapResponse], T]


@dataclass(frozen=True, slots=True)
class ImapCommand(Generic[T]):
    name: str
    arguments: tuple[object, ...] = ()
    timeout_seconds: float = 30.0
    parser: Parser[T] = field(default=lambda response: response, repr=False)

    def __post_init__(self) -> None:
        name = str(self.name or "").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9.]*", name):
            raise ValueError("invalid IMAP command name")
        timeout = float(self.timeout_seconds)
        if timeout <= 0:
            raise ValueError("IMAP command timeout must be positive")
        if not callable(self.parser):
            raise TypeError("IMAP command parser must be callable")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "arguments", tuple(self.arguments))
        object.__setattr__(self, "timeout_seconds", timeout)

    def parse(self, response: ImapResponse) -> T:
        return self.parser(response)

    @classmethod
    def identity(
        cls,
        name: str,
        arguments: tuple[object, ...] = (),
        *,
        timeout_seconds: float = 30.0,
    ) -> "ImapCommand[ImapResponse]":
        return cls(
            name=name,
            arguments=arguments,
            timeout_seconds=timeout_seconds,
            parser=lambda response: response,
        )


def _capability_tokens(value: object) -> frozenset[str]:
    raw_parts: list[str] = []

    def collect(item: object) -> None:
        if isinstance(item, Mapping):
            for child in item.values():
                collect(child)
            return
        if isinstance(item, (list, tuple, set, frozenset)):
            for child in item:
                collect(child)
            return
        if isinstance(item, (bytes, bytearray, memoryview)):
            raw_parts.append(bytes(item).decode("ascii", errors="ignore"))
            return
        if item is not None:
            raw_parts.append(str(item))

    collect(value)
    ignored = {"*", "CAPABILITY", "OK"}
    tokens: set[str] = set()
    for raw_part in raw_parts:
        for token in re.split(r"\s+", raw_part.strip()):
            normalized = token.strip("()[]").upper()
            if normalized and normalized not in ignored:
                tokens.add(normalized)
    return frozenset(tokens)


def capability_command(*, timeout_seconds: float = 10.0) -> ImapCommand[frozenset[str]]:
    return ImapCommand(
        name="CAPABILITY",
        timeout_seconds=timeout_seconds,
        parser=lambda response: _capability_tokens(response.data),
    )


def _selected_mailbox(native_key: str, response: ImapResponse) -> SelectedMailbox:
    data = response.data if isinstance(response.data, Mapping) else {}
    return SelectedMailbox(
        native_key=native_key,
        exists=int(data.get("exists", 0) or 0),
        recent=int(data.get("recent", 0) or 0),
        uidvalidity=int(data.get("uidvalidity", 0) or 0),
        uidnext=int(data.get("uidnext", 0) or 0),
        highest_modseq=int(data.get("highest_modseq", 0) or 0),
        read_only=bool(data.get("read_only", False)),
    )


def select_command(
    mailbox_native_key: str,
    *,
    timeout_seconds: float = 30.0,
) -> ImapCommand[SelectedMailbox]:
    native_key = str(mailbox_native_key or "")
    if not native_key.strip():
        raise ValueError("mailbox native key is required")
    return ImapCommand(
        name="SELECT",
        arguments=(native_key,),
        timeout_seconds=timeout_seconds,
        parser=lambda response: _selected_mailbox(native_key, response),
    )
