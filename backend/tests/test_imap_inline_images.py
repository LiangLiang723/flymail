import sys
import unittest
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from providers.base import Credentials, MessageList
from providers.base_imap import BaseIMAPReceiver


class _FakeIMAP:
    def __init__(self, raw_message: bytes):
        self.raw_message = raw_message

    def select(self, _folder, readonly=True):
        return "OK", [b"1"]

    def uid(self, command, uid, query):
        if command == "FETCH" and query == "(INTERNALDATE BODY.PEEK[])":
            header = f'{uid} (UID {uid} INTERNALDATE "30-Jul-2026 08:00:00 +0000" BODY[] {{{len(self.raw_message)}}}'.encode()
            return "OK", [(header, self.raw_message), b")"]
        raise AssertionError((command, uid, query))


class _Receiver(BaseIMAPReceiver):
    async def connect(self, credentials: Credentials) -> None:
        return None

    async def fetch_folders(self):
        return []

    async def fetch_messages(self, folder: str, page: int = 1, page_size: int = 20) -> MessageList:
        return MessageList(messages=[], total=0, page=page, page_size=page_size)

    async def mark_as_read(self, message_id: str, folder: str = "INBOX") -> None:
        return None

    async def mark_as_unread(self, message_id: str, folder: str = "INBOX") -> None:
        return None

    async def move_message(self, message_id: str, target_folder: str, source_folder: str = "INBOX") -> None:
        return None

    async def delete_message(self, message_id: str, folder: str = "INBOX") -> None:
        return None

    async def disconnect(self) -> None:
        return None


def _build_two_inline_image_message() -> bytes:
    root = MIMEMultipart("related")
    root["Subject"] = "two images"
    root["From"] = "sender@example.com"
    root["To"] = "reader@example.com"
    root["Date"] = "Thu, 30 Jul 2026 08:00:00 +0000"

    alternative = MIMEMultipart("alternative")
    alternative.attach(MIMEText("two images", "plain", "utf-8"))
    alternative.attach(MIMEText(
        '<html><body><img src="cid:image-one"><img src="CID:<image-two>"></body></html>',
        "html",
        "utf-8",
    ))
    root.attach(alternative)

    for content_id, payload in (("image-one", b"first-image"), ("image-two", b"second-image")):
        image = MIMEImage(payload, _subtype="png")
        image.add_header("Content-ID", f"<{content_id}>")
        image.add_header("Content-Disposition", "inline")
        root.attach(image)
    return root.as_bytes()


class InlineImageParsingTest(unittest.TestCase):
    def test_multiple_inline_images_keep_cid_references_and_attachment_records(self):
        receiver = _Receiver()
        receiver._conn = _FakeIMAP(_build_two_inline_image_message())

        message = receiver._fetch_detail_sync("42", "INBOX")

        self.assertIn('cid:image-one', message.body_html.lower())
        self.assertIn('cid:<image-two>', message.body_html.lower())
        self.assertNotIn('data:image/', message.body_html.lower())
        self.assertEqual(len(message.attachments), 2)
        self.assertTrue(all(item.is_inline for item in message.attachments))
        self.assertEqual({item.content_id for item in message.attachments}, {"image-one", "image-two"})
        self.assertFalse(message.has_attachments)


if __name__ == "__main__":
    unittest.main()
