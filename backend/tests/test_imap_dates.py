import unittest

import providers.base as provider_base
from providers.base_imap import BaseIMAPReceiver


class DummyReceiver(BaseIMAPReceiver):
    def __init__(self):
        self._conn = None

    async def connect(self, credentials):
        pass

    async def disconnect(self):
        pass

    async def fetch_folders(self):
        return []

    async def fetch_messages(self, folder: str = "INBOX", page: int = 1, page_size: int = 20):
        raise NotImplementedError

    async def fetch_message_detail(self, message_id: str, folder: str = "INBOX"):
        raise NotImplementedError

    async def mark_as_read(self, message_id: str, folder: str = "INBOX") -> None:
        pass

    async def mark_as_unread(self, message_id: str, folder: str = "INBOX") -> None:
        pass

    async def move_message(self, message_id: str, target_folder: str, source_folder: str = "INBOX") -> None:
        pass

    async def delete_message(self, message_id: str, folder: str = "INBOX") -> None:
        pass


class ImapDateParsingTest(unittest.TestCase):
    def test_missing_uid_raises_structured_message_not_found(self):
        self.assertTrue(hasattr(provider_base, "MessageNotFoundError"))

        class FakeConn:
            def select(self, folder, readonly=True):
                return "OK", []

            def uid(self, command, uid, query):
                return "OK", [None]

        receiver = DummyReceiver()
        receiver._conn = FakeConn()
        error_type = getattr(provider_base, "MessageNotFoundError", ValueError)

        with self.assertRaises(error_type):
            receiver._fetch_detail_sync("42", "INBOX")

    def test_batch_fetch_uses_internaldate_when_header_date_missing(self):
        receiver = DummyReceiver()
        msg_data = [
            (
                b'1 (UID 42 FLAGS (\\Seen) INTERNALDATE "12-Jul-2018 19:35:00 +0800" BODY[HEADER.FIELDS (SUBJECT FROM TO DATE)] {74}',
                b"Subject: Prize survey\r\nFrom: a@example.com\r\nTo: b@example.com\r\n\r\n",
            ),
            b")",
        ]

        messages = receiver._parse_batch_fetch_response(msg_data, "INBOX")

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].uid, 42)
        self.assertEqual(messages[0].date, "2018-07-12T11:35:00Z")

    def test_batch_fetch_prefers_header_date_over_internaldate(self):
        receiver = DummyReceiver()
        msg_data = [
            (
                b'1 (UID 43 FLAGS () INTERNALDATE "12-Jul-2018 19:35:00 +0800" BODY[HEADER.FIELDS (SUBJECT FROM TO DATE)] {121}',
                b"Subject: Header wins\r\nFrom: a@example.com\r\nTo: b@example.com\r\nDate: Fri, 13 Jul 2018 20:35:00 +0800\r\n\r\n",
            ),
            b")",
        ]

        messages = receiver._parse_batch_fetch_response(msg_data, "INBOX")

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].uid, 43)
        self.assertEqual(messages[0].date, "2018-07-13T12:35:00Z")

    def test_detail_fetch_reuses_decoded_attachment_payload_without_serializing_it(self):
        raw_email = (
            b"Subject: Attachment\r\n"
            b"From: a@example.com\r\n"
            b"To: b@example.com\r\n"
            b"Date: Thu, 12 Jul 2018 19:35:00 +0800\r\n"
            b"MIME-Version: 1.0\r\n"
            b"Content-Type: multipart/mixed; boundary=flymail\r\n\r\n"
            b"--flymail\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nBody\r\n"
            b"--flymail\r\nContent-Type: application/octet-stream\r\n"
            b"Content-Disposition: attachment; filename=report.bin\r\n"
            b"Content-Transfer-Encoding: base64\r\n\r\ncGF5bG9hZA==\r\n"
            b"--flymail--\r\n"
        )

        class FakeConn:
            def select(self, folder, readonly=True):
                return "OK", []

            def uid(self, command, uid, query):
                return "OK", [(b'1 (UID 42 BODY[] {512}', raw_email), b")"]

        receiver = DummyReceiver()
        receiver._conn = FakeConn()

        message = receiver._fetch_detail_sync("42", "INBOX")

        self.assertEqual(message.attachments[0].data, b"payload")
        self.assertNotIn("data", message.model_dump()["attachments"][0])

    def test_detail_fetch_uses_internaldate_when_header_date_missing(self):
        class FakeConn:
            def select(self, folder, readonly=True):
                return "OK", []

            def uid(self, command, uid, query):
                self.query = query
                return "OK", [
                    (
                        b'1 (UID 42 INTERNALDATE "12-Jul-2018 19:35:00 +0800" BODY[] {74}',
                        b"Subject: Prize survey\r\nFrom: a@example.com\r\nTo: b@example.com\r\n\r\nBody",
                    ),
                    b")",
                ]

        receiver = DummyReceiver()
        receiver._conn = FakeConn()

        message = receiver._fetch_detail_sync("42", "INBOX")

        self.assertEqual(message.uid, 42)
        self.assertEqual(message.date, "2018-07-12T11:35:00Z")
        self.assertIn("INTERNALDATE", receiver._conn.query)


if __name__ == "__main__":
    unittest.main()
