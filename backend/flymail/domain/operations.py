"""Immutable local-first mail operation domain contracts."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping


class OperationKind(str, Enum):
    SET_READ = "set_read"
    SET_STARRED = "set_starred"
    ADD_LABEL = "add_label"
    REMOVE_LABEL = "remove_label"
    MOVE = "move"
    ARCHIVE = "archive"
    TRASH = "trash"
    DELETE_PERMANENT = "delete_permanent"


REVERSIBLE_KINDS = frozenset(
    {
        OperationKind.SET_READ,
        OperationKind.SET_STARRED,
        OperationKind.ADD_LABEL,
        OperationKind.REMOVE_LABEL,
        OperationKind.MOVE,
        OperationKind.ARCHIVE,
        OperationKind.TRASH,
    }
)
MOTION_KINDS = frozenset(
    {
        OperationKind.MOVE,
        OperationKind.ARCHIVE,
        OperationKind.TRASH,
        OperationKind.DELETE_PERMANENT,
    }
)


def _required_text(value: str, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{label} is required")
    return normalized


@dataclass(frozen=True, slots=True)
class RemoteOperationState:
    remote_version: str
    is_read: bool
    is_starred: bool
    mailbox_native_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.is_read, bool) or not isinstance(self.is_starred, bool):
            raise TypeError("remote read and starred state must be bool")
        keys = tuple(
            dict.fromkeys(
                str(value or "").strip()
                for value in self.mailbox_native_keys
                if str(value or "").strip()
            )
        )
        object.__setattr__(self, "remote_version", str(self.remote_version or ""))
        object.__setattr__(self, "mailbox_native_keys", keys)


@dataclass(frozen=True, slots=True)
class RemoteApplyResult:
    remote_version: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "remote_version",
            _required_text(self.remote_version, "remote_version"),
        )


@dataclass(frozen=True, slots=True)
class RemoteOperationCommand:
    operation_id: str
    remote_instance_id: str
    account_id: str
    provider_key: str
    kind: OperationKind
    expected_remote_version: str
    idempotency_key: str
    desired_value: bool | None = None
    target_native_key: str = ""
    remote_action: str = ""
    allow_copy_delete: bool = False

    def __post_init__(self) -> None:
        for field_name in (
            "operation_id",
            "remote_instance_id",
            "account_id",
            "provider_key",
            "idempotency_key",
        ):
            object.__setattr__(
                self,
                field_name,
                _required_text(getattr(self, field_name), field_name),
            )
        if not isinstance(self.kind, OperationKind):
            raise TypeError("kind must be OperationKind")
        if self.desired_value is not None and not isinstance(self.desired_value, bool):
            raise TypeError("desired_value must be bool or None")
        if not isinstance(self.allow_copy_delete, bool):
            raise TypeError("allow_copy_delete must be bool")
        object.__setattr__(self, "provider_key", self.provider_key.casefold())
        object.__setattr__(self, "expected_remote_version", str(self.expected_remote_version or ""))
        object.__setattr__(self, "target_native_key", str(self.target_native_key or ""))
        object.__setattr__(
            self,
            "remote_action",
            _required_text(self.remote_action or self.kind.value, "remote_action"),
        )


@dataclass(frozen=True, slots=True)
class OperationRecord:
    id: str
    user_uid: str
    operation_group_id: str | None
    kind: OperationKind
    target_type: str
    target_id: str
    account_id: str
    remote_instance_id: str
    desired_state: Mapping[str, object]
    observed_remote_version: str
    status: str
    attempt_count: int
    idempotency_key: str
    created_at: float
    updated_at: float

    def __post_init__(self) -> None:
        for field_name in (
            "id",
            "user_uid",
            "target_type",
            "target_id",
            "account_id",
            "remote_instance_id",
            "idempotency_key",
        ):
            object.__setattr__(
                self,
                field_name,
                _required_text(getattr(self, field_name), field_name),
            )
        if not isinstance(self.kind, OperationKind):
            raise TypeError("kind must be OperationKind")
        if int(self.attempt_count) < 0:
            raise ValueError("attempt_count must be non-negative")
        for field_name in ("created_at", "updated_at"):
            value = float(getattr(self, field_name))
            if not math.isfinite(value):
                raise ValueError(f"{field_name} must be finite")
            object.__setattr__(self, field_name, value)
        object.__setattr__(self, "attempt_count", int(self.attempt_count))
        object.__setattr__(self, "desired_state", MappingProxyType(dict(self.desired_state)))
        object.__setattr__(self, "observed_remote_version", str(self.observed_remote_version or ""))
        object.__setattr__(self, "status", _required_text(self.status, "status"))


@dataclass(frozen=True, slots=True)
class OperationApplySummary:
    operation_id: str
    outcome: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "operation_id", _required_text(self.operation_id, "operation_id"))
        object.__setattr__(self, "outcome", _required_text(self.outcome, "outcome"))


@dataclass(frozen=True, slots=True)
class OperationGroupResult:
    operation_group_id: str
    operation_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "operation_group_id",
            _required_text(self.operation_group_id, "operation_group_id"),
        )
        values = tuple(_required_text(value, "operation_id") for value in self.operation_ids)
        if not values:
            raise ValueError("operation group must contain at least one operation")
        object.__setattr__(self, "operation_ids", values)
