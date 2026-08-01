"""Parse IMAP BODYSTRUCTURE values into stable, correctly numbered MIME trees."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from flymail.domain.mail import MimePart, MimeTree


_INTEGER_PATTERN = re.compile(r"^-?[0-9]+$")
_MIME_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9!#$&^_.+-]+$")


def _is_sequence(value: object) -> bool:
    return isinstance(value, (list, tuple))


def _text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray, memoryview)):
        decoded = bytes(value).decode("utf-8", errors="replace")
    else:
        decoded = str(value)
    normalized = decoded.strip()
    return normalized or None


class _SExpressionParser:
    def __init__(self, source: str) -> None:
        self.source = source
        self.position = 0

    def parse(self):
        self._skip_whitespace()
        if self.position >= len(self.source):
            raise ValueError("BODYSTRUCTURE is empty")
        value = self._parse_value()
        self._skip_whitespace()
        if self.position != len(self.source):
            raise ValueError("BODYSTRUCTURE contains trailing data")
        return value

    def _parse_value(self):
        self._skip_whitespace()
        if self.position >= len(self.source):
            raise ValueError("unexpected end of BODYSTRUCTURE")
        character = self.source[self.position]
        if character == "(":
            return self._parse_list()
        if character == '"':
            return self._parse_quoted()
        if character == ")":
            raise ValueError("unexpected closing parenthesis in BODYSTRUCTURE")
        return self._parse_atom()

    def _parse_list(self) -> list[object]:
        self.position += 1
        values: list[object] = []
        while True:
            self._skip_whitespace()
            if self.position >= len(self.source):
                raise ValueError("unbalanced BODYSTRUCTURE parentheses")
            if self.source[self.position] == ")":
                self.position += 1
                return values
            values.append(self._parse_value())

    def _parse_quoted(self) -> str:
        self.position += 1
        characters: list[str] = []
        while self.position < len(self.source):
            character = self.source[self.position]
            self.position += 1
            if character == '"':
                return "".join(characters)
            if character == "\\":
                if self.position >= len(self.source):
                    raise ValueError("unfinished escape in BODYSTRUCTURE string")
                characters.append(self.source[self.position])
                self.position += 1
            else:
                characters.append(character)
        raise ValueError("unterminated BODYSTRUCTURE string")

    def _parse_atom(self):
        start = self.position
        while self.position < len(self.source):
            character = self.source[self.position]
            if character.isspace() or character in "()":
                break
            self.position += 1
        atom = self.source[start:self.position]
        if not atom:
            raise ValueError("invalid BODYSTRUCTURE atom")
        if atom.casefold() == "nil":
            return None
        if _INTEGER_PATTERN.fullmatch(atom):
            return int(atom)
        return atom

    def _skip_whitespace(self) -> None:
        while self.position < len(self.source) and self.source[self.position].isspace():
            self.position += 1


def _normalize_raw(raw: object):
    if isinstance(raw, (bytes, bytearray, memoryview)):
        source = bytes(raw).decode("utf-8", errors="replace").strip()
        if not source:
            raise ValueError("BODYSTRUCTURE is empty")
        return _SExpressionParser(source).parse()
    if isinstance(raw, str):
        source = raw.strip()
        if not source:
            raise ValueError("BODYSTRUCTURE is empty")
        return _SExpressionParser(source).parse()
    if _is_sequence(raw):
        if not raw:
            raise ValueError("BODYSTRUCTURE is empty")
        return raw
    if raw is None:
        raise TypeError("BODYSTRUCTURE must not be None")
    raise TypeError("BODYSTRUCTURE must be a sequence, string, or bytes")


def _pairs(
    value: object,
    warnings: list[str],
    path: str,
) -> dict[str, str]:
    if value is None:
        return {}
    if not _is_sequence(value):
        warnings.append(f"{path}: parameters are not a sequence")
        return {}
    values = list(value)
    if len(values) % 2:
        warnings.append(f"{path}: parameter list has an unmatched key")
    result: dict[str, str] = {}
    for index in range(0, len(values) - 1, 2):
        key = _text(values[index])
        parameter_value = _text(values[index + 1])
        if key is None or parameter_value is None:
            warnings.append(f"{path}: ignored empty parameter")
            continue
        result[key.casefold()] = parameter_value
    return result


def _mime_token(
    value: object,
    *,
    fallback: str,
    warnings: list[str],
    path: str,
    label: str,
) -> str:
    normalized = _text(value)
    if normalized is None or not _MIME_TOKEN_PATTERN.fullmatch(normalized):
        warnings.append(f"{path}: invalid or missing {label}, defaulted to {fallback}")
        return fallback
    return normalized.casefold()


def _non_negative_int(value: object, warnings: list[str], path: str) -> int:
    if isinstance(value, bool):
        warnings.append(f"{path}: boolean is not a valid size")
        return 0
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        warnings.append(f"{path}: invalid size, defaulted to zero")
        return 0
    if number < 0:
        warnings.append(f"{path}: negative size, defaulted to zero")
        return 0
    return number


def _disposition(
    value: object,
    warnings: list[str],
    path: str,
) -> tuple[str | None, dict[str, str]]:
    if value is None:
        return None, {}
    if isinstance(value, (str, bytes, bytearray, memoryview)):
        return _text(value), {}
    if not _is_sequence(value) or not value:
        warnings.append(f"{path}: invalid disposition")
        return None, {}
    disposition = _text(value[0])
    parameters = _pairs(value[1] if len(value) > 1 else None, warnings, f"{path}.params")
    return disposition, parameters


def _multipart_components(
    node: Sequence[object],
    warnings: list[str],
    path: str,
) -> tuple[list[object], str, dict[str, str], str | None, dict[str, str]]:
    children: list[object] = []
    index = 0
    while index < len(node) and _is_sequence(node[index]):
        children.append(node[index])
        index += 1
    if not children:
        raise ValueError(f"{path}: multipart BODYSTRUCTURE has no children")

    subtype = _mime_token(
        node[index] if index < len(node) else None,
        fallback="mixed",
        warnings=warnings,
        path=path,
        label="multipart subtype",
    )
    parameters = _pairs(
        node[index + 1] if index + 1 < len(node) else None,
        warnings,
        f"{path}.params",
    )
    disposition, disposition_parameters = _disposition(
        node[index + 2] if index + 2 < len(node) else None,
        warnings,
        f"{path}.disposition",
    )
    return children, subtype, parameters, disposition, disposition_parameters


def _is_multipart_node(node: Sequence[object]) -> bool:
    return bool(node) and _is_sequence(node[0])


def _extension_disposition_index(major_type: str, subtype: str) -> int:
    if major_type == "text":
        return 9
    if major_type == "message" and subtype == "rfc822":
        return 11
    return 8


def _embedded_children(
    raw_body: object,
    parent_part: str,
    warnings: list[str],
    path: str,
) -> tuple[MimePart, ...]:
    if not _is_sequence(raw_body) or not raw_body:
        warnings.append(f"{path}: missing embedded message body")
        return ()
    if _is_multipart_node(raw_body):
        children, _subtype, _params, _disposition_value, _disposition_params = _multipart_components(
            raw_body,
            warnings,
            path,
        )
        return tuple(
            _parse_part(child, f"{parent_part}.{index}", warnings, f"{path}.{index}")
            for index, child in enumerate(children, start=1)
        )
    return (
        _parse_part(raw_body, f"{parent_part}.1", warnings, f"{path}.1"),
    )


def _parse_single_part(
    node: Sequence[object],
    imap_part: str,
    warnings: list[str],
    path: str,
) -> MimePart:
    major_type = _mime_token(
        node[0] if node else None,
        fallback="application",
        warnings=warnings,
        path=path,
        label="content type",
    )

    if major_type == "text":
        subtype_fallback = "plain"
    elif major_type == "message":
        subtype_fallback = "rfc822"
    else:
        subtype_fallback = "octet-stream"
    subtype = _mime_token(
        node[1] if len(node) > 1 else None,
        fallback=subtype_fallback,
        warnings=warnings,
        path=path,
        label="subtype",
    )

    parameters = _pairs(node[2] if len(node) > 2 else None, warnings, f"{path}.params")
    content_id = _text(node[3]) if len(node) > 3 else None
    transfer_encoding = _text(node[5]) if len(node) > 5 else None
    size = _non_negative_int(node[6] if len(node) > 6 else None, warnings, f"{path}.size")

    disposition_index = _extension_disposition_index(major_type, subtype)
    disposition_value, disposition_parameters = _disposition(
        node[disposition_index] if len(node) > disposition_index else None,
        warnings,
        f"{path}.disposition",
    )
    filename = disposition_parameters.get("filename") or parameters.get("name")
    children: tuple[MimePart, ...] = ()
    if major_type == "message" and subtype == "rfc822":
        embedded_body = node[8] if len(node) > 8 else None
        children = _embedded_children(
            embedded_body,
            imap_part,
            warnings,
            f"{path}.message",
        )

    return MimePart(
        imap_part=imap_part,
        content_type=f"{major_type}/{subtype}",
        charset=parameters.get("charset"),
        transfer_encoding=transfer_encoding,
        disposition=disposition_value,
        filename=filename,
        content_id=content_id,
        size=size,
        children=children,
    )


def _parse_part(
    raw_node: object,
    imap_part: str,
    warnings: list[str],
    path: str,
) -> MimePart:
    if not _is_sequence(raw_node) or not raw_node:
        raise ValueError(f"{path}: MIME part is not a non-empty sequence")
    node = list(raw_node)
    if _is_multipart_node(node):
        children_raw, subtype, _params, disposition, disposition_parameters = _multipart_components(
            node,
            warnings,
            path,
        )
        children = tuple(
            _parse_part(child, f"{imap_part}.{index}", warnings, f"{path}.{index}")
            for index, child in enumerate(children_raw, start=1)
        )
        return MimePart(
            imap_part=imap_part,
            content_type=f"multipart/{subtype}",
            charset=None,
            transfer_encoding=None,
            disposition=disposition,
            filename=disposition_parameters.get("filename"),
            content_id=None,
            size=sum(child.size for child in children),
            children=children,
        )
    return _parse_single_part(node, imap_part, warnings, path)


def parse_bodystructure(raw: object) -> MimeTree:
    """Parse BODYSTRUCTURE lists or raw IMAP S-expressions.

    Section identifiers are derived exclusively from BODYSTRUCTURE tree
    positions. No decoded-message traversal is involved.
    """

    normalized = _normalize_raw(raw)
    if not _is_sequence(normalized) or not normalized:
        raise ValueError("BODYSTRUCTURE root must be a non-empty sequence")
    root = list(normalized)
    warnings: list[str] = []

    if _is_multipart_node(root):
        children_raw, subtype, _params, _disposition_value, _disposition_params = _multipart_components(
            root,
            warnings,
            "root",
        )
        parts = tuple(
            _parse_part(child, str(index), warnings, f"root.{index}")
            for index, child in enumerate(children_raw, start=1)
        )
        root_content_type = f"multipart/{subtype}"
    else:
        part = _parse_single_part(root, "1", warnings, "root.1")
        parts = (part,)
        root_content_type = part.content_type

    return MimeTree(
        root_content_type=root_content_type,
        parts=parts,
        warnings=tuple(warnings),
    )
