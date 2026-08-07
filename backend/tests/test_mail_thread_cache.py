import unittest

from models import Account
from providers.base import Message
from services.mail_cache import _messages_to_cached
from services.message_threads import build_thread_key


class MailThreadCacheTests(unittest.TestCase):
    def test_messages_to_cached_persists_thread_metadata(self):
        account = Account(
            id="acc-1",
            user_uid="user-1",
            email="me@example.com",
            provider="custom",
        )
        message = Message(
            id="42",
            uid=42,
            subject="Re: Project",
            from_addr="alice@example.com",
            to_addr="me@example.com",
            date="2026-08-07T01:00:00Z",
            folder="INBOX",
            message_id="<child@example.com>",
            in_reply_to="<parent@example.com>",
            references_header="<root@example.com> <parent@example.com>",
        )

        cached = _messages_to_cached([message], account)[0]

        self.assertEqual(cached.message_id, "<child@example.com>")
        self.assertEqual(cached.in_reply_to, "<parent@example.com>")
        self.assertEqual(cached.references_header, "<root@example.com> <parent@example.com>")
        self.assertEqual(
            cached.thread_key,
            build_thread_key(
                "acc-1",
                "<child@example.com>",
                "<parent@example.com>",
                "<root@example.com> <parent@example.com>",
                "Re: Project",
            ),
        )


if __name__ == "__main__":
    unittest.main()
