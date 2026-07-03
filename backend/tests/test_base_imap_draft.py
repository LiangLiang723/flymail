import asyncio
import unittest

from providers.base_imap import BaseIMAPReceiver


class Receiver(BaseIMAPReceiver):
    async def connect(self, credentials):
        pass

    async def disconnect(self):
        pass

    async def fetch_folders(self):
        return []

    async def fetch_messages(self, folder: str = "INBOX", page: int = 1, page_size: int = 20):
        return None


class BaseIMAPDraftTest(unittest.TestCase):
    def test_save_draft_returns_append_uid_when_available(self):
        class Conn:
            def append(self, mailbox, flags, date_time, message):
                return "OK", [b"[APPENDUID 12345 67890] Saved"]

        receiver = Receiver()
        receiver._conn = Conn()

        uid = asyncio.run(receiver.save_draft(b"message", "Drafts"))

        self.assertEqual(uid, 67890)


if __name__ == "__main__":
    unittest.main()
