from __future__ import annotations

import ast
import dataclasses
import json
import unittest
from pathlib import Path

from flymail.domain.mail import MimePart, MimeTree, PartSelection
from flymail.providers.core.bodystructure import parse_bodystructure
from flymail.providers.core.mime_parts import (
    build_partial_fetch,
    select_message_parts,
    validate_imap_part,
)


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "imap"


def load_fixture(name: str):
    return json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))


def flatten_parts(parts: tuple[MimePart, ...]) -> tuple[MimePart, ...]:
    flattened: list[MimePart] = []

    def visit(part: MimePart) -> None:
        flattened.append(part)
        for child in part.children:
            visit(child)

    for part in parts:
        visit(part)
    return tuple(flattened)


class MimePartModelTests(unittest.TestCase):
    def test_mime_part_is_immutable_and_normalizes_protocol_fields(self):
        part = MimePart(
            imap_part="1.2",
            content_type=" Text/HTML ",
            charset=" UTF-8 ",
            transfer_encoding=" Quoted-Printable ",
            disposition=" INLINE ",
            filename=" page.html ",
            content_id=" <Page-123> ",
            size=42,
        )

        self.assertEqual(part.imap_part, "1.2")
        self.assertEqual(part.content_type, "text/html")
        self.assertEqual(part.charset, "utf-8")
        self.assertEqual(part.transfer_encoding, "quoted-printable")
        self.assertEqual(part.disposition, "inline")
        self.assertEqual(part.filename, "page.html")
        self.assertEqual(part.content_id, "Page-123")
        self.assertEqual(part.size, 42)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            part.size = 1  # type: ignore[misc]

    def test_mime_part_rejects_invalid_part_or_negative_size(self):
        with self.assertRaisesRegex(ValueError, "part"):
            MimePart("1.0", "text/plain", None, None, None, None, None, 0)
        with self.assertRaisesRegex(ValueError, "size"):
            MimePart("1", "text/plain", None, None, None, None, None, -1)

    def test_tree_walk_and_lookup_are_stable(self):
        tree = parse_bodystructure(load_fixture("mixed_attachment.json"))
        self.assertEqual(
            tuple(part.imap_part for part in tree.walk()),
            ("1", "1.1", "1.2", "2"),
        )
        self.assertEqual(tree.get("1.2").content_type, "text/html")
        self.assertIsNone(tree.get("3"))


class BodyStructureParsingTests(unittest.TestCase):
    def test_plain_text_single_part_uses_section_one(self):
        tree = parse_bodystructure(load_fixture("plain_text.json"))

        self.assertIsInstance(tree, MimeTree)
        self.assertEqual(tree.root_content_type, "text/plain")
        self.assertEqual(tree.warnings, ())
        self.assertEqual(len(tree.parts), 1)
        part = tree.parts[0]
        self.assertEqual(part.imap_part, "1")
        self.assertEqual(part.content_type, "text/plain")
        self.assertEqual(part.charset, "utf-8")
        self.assertEqual(part.transfer_encoding, "7bit")
        self.assertEqual(part.size, 42)
        self.assertEqual(part.children, ())

    def test_top_level_alternative_children_are_numbered_one_and_two(self):
        tree = parse_bodystructure(load_fixture("alternative.json"))

        self.assertEqual(tree.root_content_type, "multipart/alternative")
        self.assertEqual(
            tuple((part.imap_part, part.content_type) for part in tree.parts),
            (("1", "text/plain"), ("2", "text/html")),
        )

    def test_nested_multipart_preserves_container_and_child_part_numbers(self):
        tree = parse_bodystructure(load_fixture("mixed_attachment.json"))

        self.assertEqual(tree.root_content_type, "multipart/mixed")
        self.assertEqual(len(tree.parts), 2)
        alternative = tree.parts[0]
        attachment = tree.parts[1]
        self.assertEqual(alternative.imap_part, "1")
        self.assertEqual(alternative.content_type, "multipart/alternative")
        self.assertEqual(
            tuple((part.imap_part, part.content_type) for part in alternative.children),
            (("1.1", "text/plain"), ("1.2", "text/html")),
        )
        self.assertEqual(attachment.imap_part, "2")
        self.assertEqual(attachment.content_type, "application/pdf")
        self.assertEqual(attachment.disposition, "attachment")
        self.assertEqual(attachment.filename, "report.pdf")
        self.assertEqual(attachment.size, 5000)

    def test_related_tree_normalizes_inline_metadata_and_cid(self):
        tree = parse_bodystructure(load_fixture("related.json"))

        self.assertEqual(tree.root_content_type, "multipart/related")
        html, image = tree.parts
        self.assertEqual((html.imap_part, html.content_type), ("1", "text/html"))
        self.assertEqual((image.imap_part, image.content_type), ("2", "image/png"))
        self.assertEqual(image.disposition, "inline")
        self.assertEqual(image.filename, "logo.png")
        self.assertEqual(image.content_id, "logo-123")

    def test_message_rfc822_keeps_embedded_tree_but_is_numbered_as_attachment(self):
        tree = parse_bodystructure(load_fixture("rfc822.json"))
        message_part = tree.get("2")

        self.assertIsNotNone(message_part)
        self.assertEqual(message_part.content_type, "message/rfc822")
        self.assertEqual(message_part.filename, "forwarded.eml")
        self.assertEqual(message_part.disposition, "attachment")
        self.assertEqual(len(message_part.children), 1)
        self.assertEqual(message_part.children[0].imap_part, "2.1")
        self.assertEqual(message_part.children[0].content_type, "text/plain")

    def test_missing_attachment_filename_is_preserved_as_none(self):
        tree = parse_bodystructure(load_fixture("missing_filename.json"))
        part = tree.parts[0]

        self.assertEqual(part.imap_part, "1")
        self.assertEqual(part.content_type, "application/octet-stream")
        self.assertEqual(part.disposition, "attachment")
        self.assertIsNone(part.filename)

    def test_malformed_but_recoverable_structure_returns_warning_and_defaults(self):
        tree = parse_bodystructure(load_fixture("malformed_recoverable.json"))
        part = tree.parts[0]

        self.assertEqual(part.imap_part, "1")
        self.assertEqual(part.content_type, "text/plain")
        self.assertIsNone(part.charset)
        self.assertEqual(part.transfer_encoding, "8bit")
        self.assertEqual(part.size, 0)
        self.assertTrue(tree.warnings)
        self.assertTrue(any("subtype" in warning for warning in tree.warnings))
        self.assertTrue(any("size" in warning for warning in tree.warnings))

    def test_invalid_content_type_tokens_are_recovered_with_warning(self):
        tree = parse_bodystructure(
            ["T@XT", "PL AIN", None, None, None, "7BIT", 12]
        )

        self.assertEqual(tree.parts[0].content_type, "application/octet-stream")
        self.assertTrue(any("content type" in warning for warning in tree.warnings))
        self.assertTrue(any("subtype" in warning for warning in tree.warnings))

    def test_raw_parenthesized_bodystructure_and_bytes_are_parsed(self):
        raw = (
            '("TEXT" "PLAIN" ("CHARSET" "UTF-8") NIL NIL '
            '"QUOTED-PRINTABLE" 64 4)'
        )
        text_tree = parse_bodystructure(raw)
        bytes_tree = parse_bodystructure(raw.encode("ascii"))

        for tree in (text_tree, bytes_tree):
            self.assertEqual(tree.parts[0].imap_part, "1")
            self.assertEqual(tree.parts[0].content_type, "text/plain")
            self.assertEqual(tree.parts[0].charset, "utf-8")
            self.assertEqual(tree.parts[0].size, 64)

    def test_raw_parenthesized_nested_structure_uses_tree_position(self):
        raw = (
            '(("TEXT" "PLAIN" ("CHARSET" "UTF-8") NIL NIL "7BIT" 10 1) '
            '("TEXT" "HTML" ("CHARSET" "UTF-8") NIL NIL "7BIT" 20 2) '
            '"ALTERNATIVE" ("BOUNDARY" "alt"))'
        )
        tree = parse_bodystructure(raw)

        self.assertEqual(tree.root_content_type, "multipart/alternative")
        self.assertEqual(tuple(part.imap_part for part in tree.parts), ("1", "2"))

    def test_unbalanced_or_empty_structure_is_rejected(self):
        for raw in (None, "", "(", '("TEXT" "PLAIN"'):
            with self.subTest(raw=raw):
                with self.assertRaises((TypeError, ValueError)):
                    parse_bodystructure(raw)


class PartSelectionTests(unittest.TestCase):
    def test_alternative_preserves_both_text_and_html_body_references(self):
        selection = select_message_parts(
            parse_bodystructure(load_fixture("alternative.json"))
        )

        self.assertIsInstance(selection, PartSelection)
        self.assertEqual(selection.text_part.imap_part, "1")
        self.assertEqual(selection.html_part.imap_part, "2")
        self.assertEqual(selection.body_parts, (selection.text_part, selection.html_part))
        self.assertEqual(selection.attachment_parts, ())
        self.assertEqual(selection.inline_candidates, ())
        self.assertEqual(selection.inline_parts, ())

    def test_nested_alternative_body_and_ordinary_attachment_are_separated(self):
        selection = select_message_parts(
            parse_bodystructure(load_fixture("mixed_attachment.json"))
        )

        self.assertEqual(selection.text_part.imap_part, "1.1")
        self.assertEqual(selection.html_part.imap_part, "1.2")
        self.assertEqual(
            tuple(part.imap_part for part in selection.attachment_parts),
            ("2",),
        )
        self.assertNotIn(selection.attachment_parts[0], selection.body_parts)

    def test_related_inline_image_fetch_requires_actual_case_insensitive_cid_reference(self):
        tree = parse_bodystructure(load_fixture("related.json"))

        without_html = select_message_parts(tree)
        unrelated_html = select_message_parts(tree, '<img src="cid:other">')
        referenced = select_message_parts(
            tree,
            '<div style="background:url(CID:logo-123)"></div>',
        )

        self.assertEqual(
            tuple(part.imap_part for part in without_html.inline_candidates),
            ("2",),
        )
        self.assertEqual(without_html.inline_parts, ())
        self.assertEqual(unrelated_html.inline_parts, ())
        self.assertEqual(
            tuple(part.imap_part for part in referenced.inline_parts),
            ("2",),
        )
        self.assertEqual(referenced.attachment_parts, ())

    def test_inline_disposition_outside_related_context_remains_attachment_metadata(self):
        raw = [
            "IMAGE",
            "PNG",
            ["NAME", "orphan.png"],
            "<orphan>",
            None,
            "BASE64",
            55,
            None,
            ["INLINE", ["FILENAME", "orphan.png"]],
        ]
        selection = select_message_parts(
            parse_bodystructure(raw),
            '<img src="cid:orphan">',
        )

        self.assertEqual(selection.inline_candidates, ())
        self.assertEqual(selection.inline_parts, ())
        self.assertEqual(
            tuple(part.imap_part for part in selection.attachment_parts),
            ("1",),
        )

    def test_nested_message_is_attachment_and_embedded_body_is_not_selected(self):
        selection = select_message_parts(
            parse_bodystructure(load_fixture("rfc822.json"))
        )

        self.assertEqual(selection.text_part.imap_part, "1")
        self.assertIsNone(selection.html_part)
        self.assertEqual(
            tuple(part.imap_part for part in selection.nested_message_parts),
            ("2",),
        )
        self.assertEqual(
            tuple(part.imap_part for part in selection.attachment_parts),
            ("2",),
        )
        self.assertNotIn("2.1", {part.imap_part for part in selection.body_parts})

    def test_attachment_without_filename_remains_attachment_metadata(self):
        selection = select_message_parts(
            parse_bodystructure(load_fixture("missing_filename.json"))
        )

        self.assertIsNone(selection.text_part)
        self.assertIsNone(selection.html_part)
        self.assertEqual(len(selection.attachment_parts), 1)
        self.assertIsNone(selection.attachment_parts[0].filename)


class PartialFetchTests(unittest.TestCase):
    def test_valid_part_and_partial_range_build_exact_peek_syntax(self):
        self.assertEqual(validate_imap_part("1"), "1")
        self.assertEqual(validate_imap_part("1.2.10"), "1.2.10")
        self.assertEqual(
            build_partial_fetch("1.2", 0, 4096),
            "BODY.PEEK[1.2]<0.4096>",
        )
        self.assertEqual(
            build_partial_fetch("2.1", 4096, 1024),
            "BODY.PEEK[2.1]<4096.1024>",
        )

    def test_invalid_part_number_is_rejected(self):
        for part in (
            None,
            1,
            "",
            "0",
            "01",
            "1.0",
            "1.02",
            ".1",
            "1.",
            "1..2",
            "BODY[]",
            "1]",
            "1 2",
            "HEADER",
        ):
            with self.subTest(part=part):
                with self.assertRaisesRegex((TypeError, ValueError), "part"):
                    validate_imap_part(part)  # type: ignore[arg-type]

    def test_invalid_partial_range_is_rejected(self):
        for offset, count in (
            (-1, 1),
            (0, 0),
            (0, -1),
            (True, 1),
            (0, True),
            (1.5, 1),
            (0, 1.5),
            ("0", 1),
        ):
            with self.subTest(offset=offset, count=count):
                with self.assertRaisesRegex((TypeError, ValueError), "partial"):
                    build_partial_fetch("1", offset, count)

    def test_partial_fetch_module_never_constructs_full_message_peek(self):
        source_path = (
            Path(__file__).resolve().parents[2]
            / "flymail"
            / "providers"
            / "core"
            / "mime_parts.py"
        )
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        literals = {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        self.assertNotIn("BODY.PEEK[]", literals)


if __name__ == "__main__":
    unittest.main()
