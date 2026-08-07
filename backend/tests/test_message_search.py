import unittest

from services.message_search import parse_message_search


class MessageSearchParserTests(unittest.TestCase):
    def test_parses_common_operators_and_free_text(self):
        parsed = parse_message_search(
            '季度 报告 from:"Alice Zhang <alice@example.com>" '
            'to:bob@example.com subject:"项目进度" after:2026-07-01 before:2026-08-01 '
            'has:attachment is:unread is:starred'
        )

        self.assertEqual(parsed.free_text, "季度 报告")
        self.assertEqual(parsed.from_addr, "Alice Zhang <alice@example.com>")
        self.assertEqual(parsed.to_addr, "bob@example.com")
        self.assertEqual(parsed.subject, "项目进度")
        self.assertEqual(parsed.after, "2026-07-01")
        self.assertEqual(parsed.before, "2026-08-01")
        self.assertTrue(parsed.has_attachment)
        self.assertEqual(parsed.read_state, "unread")
        self.assertTrue(parsed.starred)

    def test_last_read_operator_wins(self):
        parsed = parse_message_search("is:unread is:read")
        self.assertEqual(parsed.read_state, "read")
        self.assertEqual(parsed.free_text, "")

    def test_invalid_date_and_unknown_operator_remain_free_text(self):
        parsed = parse_message_search("after:2026-99-99 label:finance hello")
        self.assertEqual(parsed.after, "")
        self.assertEqual(parsed.free_text, "after:2026-99-99 label:finance hello")

    def test_plain_quoted_phrase_remains_free_text_without_quotes(self):
        parsed = parse_message_search('"exact phrase" status')
        self.assertEqual(parsed.free_text, "exact phrase status")


if __name__ == "__main__":
    unittest.main()
