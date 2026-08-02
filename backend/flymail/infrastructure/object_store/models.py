"""Value objects used by FlyMail V2 content-addressed storage."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from flymail.domain.enums import ObjectKind


class ObjectVerificationStatus(str, Enum):
    READY = "ready"
    MISSING = "missing"
    CORRUPT = "corrupt"


@dataclass(frozen=True, slots=True)
class StoredObject:
    content_sha256: str
    kind: ObjectKind
    original_size_bytes: int
    stored_size_bytes: int
    relative_path: str
    path: Path = field(repr=False)
    created: bool = False
    compression: str = "none"


@dataclass(frozen=True, slots=True)
class ObjectVerification:
    content_sha256: str
    status: ObjectVerificationStatus
    path: Path = field(repr=False)
    expected_size_bytes: int | None = None
    actual_size_bytes: int | None = None
    actual_sha256: str = ""


@dataclass(frozen=True, slots=True)
class ContentObjectRecord:
    content_sha256: str
    object_kind: str
    compression: str
    original_size_bytes: int
    stored_size_bytes: int
    relative_path: str
    verified_at: float | None
    created_at: float


@dataclass(frozen=True, slots=True)
class BodyEvictionCandidate:
    content_sha256: str
    stored_size_bytes: int
    last_accessed_at: float


@dataclass(frozen=True, slots=True)
class DetachedBodyObject:
    content_sha256: str
    logical_bytes: int
    message_ids: tuple[str, ...]
    removed_reference_count: int


@dataclass(frozen=True, slots=True)
class AttachmentEvictionCandidate:
    content_sha256: str
    stored_size_bytes: int
    last_accessed_at: float


@dataclass(frozen=True, slots=True)
class DetachedAttachmentObject:
    content_sha256: str
    logical_bytes: int
    attachment_ids: tuple[str, ...]
    removed_reference_count: int


@dataclass(frozen=True, slots=True)
class EvictionResult:
    before_bytes: int
    after_bytes: int
    logical_bytes_released: int = 0
    physical_bytes_released: int = 0
    message_count: int = 0
    object_count: int = 0
