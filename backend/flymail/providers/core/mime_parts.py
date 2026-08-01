"""Select fetchable MIME parts and build safe IMAP partial FETCH sections."""

from __future__ import annotations

import operator
import re
from urllib.parse import unquote

from flymail.domain.mail import MimePart, MimeTree, PartSelection


_IMAP_PART_PATTERN = re.compile(r"^[1-9][0-9]*(?:\.[1-9][0-9]*)*$")
_CID_REFERENCE_PATTERN = re.compile(
    r"(?i)\bcid\s*:\s*<?([^'\"<>\s)>]+)>?"
)


def validate_imap_part(imap_part: str) -> str:
    if not isinstance(imap_part, str):
        raise TypeError("invalid IMAP part: part must be a string")
    normalized = imap_part.strip()
    if not _IMAP_PART_PATTERN.fullmatch(normalized):
        raise ValueError(f"invalid IMAP part: {normalized!r}")
    return normalized


def build_partial_fetch(imap_part: str, offset: int, count: int) -> str:
    part = validate_imap_part(imap_part)
    if isinstance(offset, bool) or isinstance(count, bool):
        raise TypeError("invalid partial range")
    try:
        normalized_offset = operator.index(offset)
        normalized_count = operator.index(count)
    except TypeError as exc:
        raise TypeError("invalid partial range") from exc
    if normalized_offset < 0 or normalized_count <= 0:
        raise ValueError("invalid partial range")
    return f"BODY.PEEK[{part}]<{normalized_offset}.{normalized_count}>"


def _referenced_content_ids(html_body: str | None) -> frozenset[str]:
    if not html_body:
        return frozenset()
    return frozenset(
        unquote(match).strip().strip("<>").casefold()
        for match in _CID_REFERENCE_PATTERN.findall(str(html_body))
        if unquote(match).strip().strip("<>")
    )


def _is_attachment(part: MimePart) -> bool:
    return part.disposition == "attachment" or part.filename is not None


def select_message_parts(
    tree: MimeTree,
    html_body: str | None = None,
) -> PartSelection:
    if not isinstance(tree, MimeTree):
        raise TypeError("tree must be MimeTree")

    text_part: MimePart | None = None
    html_part: MimePart | None = None
    inline_candidates: list[MimePart] = []
    attachment_parts: list[MimePart] = []
    nested_message_parts: list[MimePart] = []

    def visit(part: MimePart, *, related_context: bool) -> None:
        nonlocal text_part, html_part

        if part.is_nested_message:
            nested_message_parts.append(part)
            attachment_parts.append(part)
            return

        if part.is_multipart:
            child_related_context = related_context or part.content_type == "multipart/related"
            for child in part.children:
                visit(child, related_context=child_related_context)
            return

        attached = _is_attachment(part)
        if part.content_type == "text/plain" and not attached:
            if text_part is None:
                text_part = part
            return
        if part.content_type == "text/html" and not attached:
            if html_part is None:
                html_part = part
            return

        inline_candidate = (
            related_context
            and part.content_id is not None
            and part.content_type.startswith("image/")
            and part.disposition != "attachment"
        )
        if inline_candidate:
            inline_candidates.append(part)
            return

        attachment_parts.append(part)

    root_related_context = tree.root_content_type == "multipart/related"
    for root_part in tree.parts:
        visit(root_part, related_context=root_related_context)

    referenced_ids = _referenced_content_ids(html_body)
    inline_parts = tuple(
        part
        for part in inline_candidates
        if part.content_id is not None and part.content_id.casefold() in referenced_ids
    )

    return PartSelection(
        text_part=text_part,
        html_part=html_part,
        inline_candidates=tuple(inline_candidates),
        inline_parts=inline_parts,
        attachment_parts=tuple(attachment_parts),
        nested_message_parts=tuple(nested_message_parts),
    )
