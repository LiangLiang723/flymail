import unittest

from services.message_threads import (
    build_thread_key,
    normalize_message_id,
    normalize_subject_for_thread,
)


class MessageThreadTests(unittest.TestCase):
    def test_normalize_message_id_wraps_bare_value(self):
        self.assertEqual(normalize_message_id("abc@example.com"), "<abc@example.com>")
        self.assertEqual(normalize_message_id(" <abc@example.com> "), "<abc@example.com>")

    def test_references_root_wins_over_in_reply_to(self):
        from_references = build_thread_key(
            account_id="acc-1",
            message_id="<child@example.com>",
            in_reply_to="<parent@example.com>",
            references="<root@example.com> <parent@example.com>",
            subject="Re: Project",
        )
        root_message = build_thread_key(
            account_id="acc-1",
            message_id="<root@example.com>",
            in_reply_to="",
            references="",
            subject="Project",
        )
        self.assertEqual(from_references, root_message)

    def test_in_reply_to_matches_parent_message_thread(self):
        reply = build_thread_key(
            account_id="acc-1",
            message_id="<reply@example.com>",
            in_reply_to="parent@example.com",
            references="",
            subject="Re: Status",
        )
        parent = build_thread_key(
            account_id="acc-1",
            message_id="<parent@example.com>",
            in_reply_to="",
            references="",
            subject="Status",
        )
        self.assertEqual(reply, parent)

    def test_subject_fallback_is_normalized_and_account_scoped(self):
        self.assertEqual(normalize_subject_for_thread(" Re:  Fwd:   Weekly   Report "), "weekly report")
        key_one = build_thread_key("acc-1", "", "", "", "Re: Weekly Report")
        key_same = build_thread_key("acc-1", "", "", "", "FW: weekly   report")
        key_other_account = build_thread_key("acc-2", "", "", "", "Weekly Report")
        self.assertEqual(key_one, key_same)
        self.assertNotEqual(key_one, key_other_account)

    def test_empty_thread_metadata_and_subject_stays_empty(self):
        self.assertEqual(build_thread_key("acc-1", "", "", "", ""), "")


if __name__ == "__main__":
    unittest.main()
