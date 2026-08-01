"""Immutable mail structure domain models for FlyMail V2."""

from __future__ import annotations

import re
from dataclasses import dataclass


_IMAP_PART_PATTERN = re.compile(r"^[1-9][0-9]*(?:\.[1-9][0-9]*)*$")
_CONTENT_TYPE_PATTERN = re.compile(r"^[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+$")


def _optional_text(value: str | None, *, casefold: bool = False) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    return normalized.casefold() if casefold else normalized


@dataclass(frozen=True, slots=True)
class MimePart:
    imap_part: str
    content_type: str
    charset: str | None
    transfer_encoding: str | None
    disposition: str | None
    filename: str | None
    content_id: str | None
    size: int
    children: tuple["MimePart", ...] = ()

    def __post_init__(self) -> None:
        imap_part = str(self.imap_part or "").strip()
        if not _IMAP_PART_PATTERN.fullmatch(imap_part):
            raise ValueError(f"invalid IMAP part: {imap_part!r}")

        content_type = str(self.content_type or "").strip().casefold()
        if not _CONTENT_TYPE_PATTERN.fullmatch(content_type):
            raise ValueError(f"invalid content type: {content_type!r}")

        if isinstance(self.size, bool):
            raise TypeError("part size must be an integer")
        size = int(self.size)
        if size < 0:
            raise ValueError("part size must be non-negative")

        children = tuple(self.children)
        if any(not isinstance(child, MimePart) for child in children):
            raise TypeError("part children must be MimePart values")
        child_ids = [child.imap_part for child in children]
        if len(set(child_ids)) != len(child_ids):
            raise ValueError("part children must have unique IMAP parts")
        expected_prefix = f"{imap_part}."
        if any(not child.imap_part.startswith(expected_prefix) for child in children):
            raise ValueError("child IMAP part must extend parent part")

        content_id = _optional_text(self.content_id)
        if content_id is not None:
            content_id = content_id.strip().strip("<>").strip() or None

        object.__setattr__(self, "imap_part", imap_part)
        object.__setattr__(self, "content_type", content_type)
        object.__setattr__(self, "charset", _optional_text(self.charset, casefold=True))
        object.__setattr__(
            self,
            "transfer_encoding",
            _optional_text(self.transfer_encoding, casefold=True),
        )
        object.__setattr__(self, "disposition", _optional_text(self.disposition, casefold=True))
        object.__setattr__(self, "filename", _optional_text(self.filename))
        object.__setattr__(self, "content_id", content_id)
        object.__setattr__(self, "size", size)
        object.__setattr__(self, "children", children)

    @property
    def is_multipart(self) -> bool:
        return self.content_type.startswith("multipart/")

    @property
    def is_nested_message(self) -> bool:
        return self.content_type == "message/rfc822"


@dataclass(frozen=True, slots=True)
class MimeTree:
    root_content_type: str
    parts: tuple[MimePart, ...]
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        root_content_type = str(self.root_content_type or "").strip().casefold()
        if not _CONTENT_TYPE_PATTERN.fullmatch(root_content_type):
            raise ValueError(f"invalid root content type: {root_content_type!r}")
        parts = tuple(self.parts)
        if not parts:
            raise ValueError("MIME tree requires at least one part")
        if any(not isinstance(part, MimePart) for part in parts):
            raise TypeError("MIME tree parts must be MimePart values")
        warnings = tuple(str(warning).strip() for warning in self.warnings if str(warning).strip())

        flattened = tuple(self._walk_parts(parts))
        part_ids = [part.imap_part for part in flattened]
        if len(set(part_ids)) != len(part_ids):
            raise ValueError("MIME tree contains duplicate IMAP parts")

        object.__setattr__(self, "root_content_type", root_content_type)
        object.__setattr__(self, "parts", parts)
        object.__setattr__(self, "warnings", warnings)

    @staticmethod
    def _walk_parts(parts: tuple[MimePart, ...]):
        for part in parts:
            yield part
            yield from MimeTree._walk_parts(part.children)

    def walk(self) -> tuple[MimePart, ...]:
        return tuple(self._walk_parts(self.parts))

    def get(self, imap_part: str) -> MimePart | None:
        normalized = str(imap_part or "").strip()
        return next((part for part in self._walk_parts(self.parts) if part.imap_part == normalized), None)


@dataclass(frozen=True, slots=True)
class PartSelection:
    text_part: MimePart | None = None
    html_part: MimePart | None = None
    inline_candidates: tuple[MimePart, ...] = ()
    inline_parts: tuple[MimePart, ...] = ()
    attachment_parts: tuple[MimePart, ...] = ()
    nested_message_parts: tuple[MimePart, ...] = ()

    def __post_init__(self) -> None:
        for field_name in (
            "inline_candidates",
            "inline_parts",
            "attachment_parts",
            "nested_message_parts",
        ):
            values = tuple(getattr(self, field_name))
            if any(not isinstance(value, MimePart) for value in values):
                raise TypeError(f"{field_name} must contain MimePart values")
            if len({value.imap_part for value in values}) != len(values):
                raise ValueError(f"{field_name} must not contain duplicate parts")
            object.__setattr__(self, field_name, values)
        for field_name in ("text_part", "html_part"):
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, MimePart):
                raise TypeError(f"{field_name} must be MimePart or None")

        candidate_ids = {part.imap_part for part in self.inline_candidates}
        if any(part.imap_part not in candidate_ids for part in self.inline_parts):
            raise ValueError("selected inline parts must be inline candidates")

    @property
    def body_parts(self) -> tuple[MimePart, ...]:
        return tuple(part for part in (self.text_part, self.html_part) if part is not None)
