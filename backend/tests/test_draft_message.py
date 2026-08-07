import importlib
import importlib.util
import sys
import unittest
from email import message_from_bytes
from pathlib import Path


def _load_draft_module():
    module_path = Path(__file__).resolve().parents[1] / "services" / "draft.py"
    spec = importlib.util.spec_from_file_location("draft_for_test", module_path)
    module = importlib.util.module_from_spec(spec)
    backend_dir = str(Path(__file__).resolve().parents[1])
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    spec.loader.exec_module(module)
    return module


class DraftMessageTest(unittest.TestCase):
    def test_draft_message_preserves_empty_paragraphs(self):
        draft = _load_draft_module()
        raw = draft._build_draft_message(
            from_email="sender@example.com",
            from_name="sender@example.com",
            to=["to@example.com"],
            cc=[],
            bcc=[],
            subject="draft",
            body_html="<p>a</p><p></p><p>b</p>",
        )

        msg = message_from_bytes(raw)
        html_parts = []
        plain_parts = []
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                html_parts.append(part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8"))
            if part.get_content_type() == "text/plain":
                plain_parts.append(part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8"))

        self.assertEqual(html_parts, ["<p>a</p><p><br></p><p>b</p>"])
        self.assertEqual(plain_parts, ["a\n\nb"])

    def test_draft_message_embeds_inline_signature_image(self):
        draft = _load_draft_module()
        mime_parts = importlib.import_module("services.mime_parts")
        inline = mime_parts.InlineImagePart(
            content_id="draft-image@flymail",
            data=b"draft-inline-image",
            content_type="image/webp",
            filename="signature.webp",
        )

        raw = draft._build_draft_message(
            from_email="sender@example.com",
            from_name="sender@example.com",
            to=["to@example.com"],
            cc=[],
            bcc=[],
            subject="draft inline",
            body_html='<p>x</p><img src="cid:draft-image@flymail">',
            inline_images=[inline],
        )

        msg = message_from_bytes(raw)
        image = next(part for part in msg.walk() if part.get_content_type() == "image/webp")
        html = next(part for part in msg.walk() if part.get_content_type() == "text/html")
        html_text = html.get_payload(decode=True).decode(html.get_content_charset() or "utf-8")
        self.assertIn('src="cid:draft-image@flymail"', html_text)
        self.assertEqual(image.get("Content-ID"), "<draft-image@flymail>")
        self.assertEqual(image.get_content_disposition(), "inline")
        self.assertEqual(image.get_payload(decode=True), b"draft-inline-image")


if __name__ == "__main__":
    unittest.main()
