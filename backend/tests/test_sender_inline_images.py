import re
import unittest
from pathlib import Path


class SenderInlineImageContractTest(unittest.TestCase):
    def test_all_smtp_senders_use_shared_inline_mime_builder(self):
        backend_root = Path(__file__).resolve().parents[1]
        sender_paths = [
            backend_root / "providers" / provider / "sender.py"
            for provider in ("gmail", "outlook", "qq", "netease", "icloud", "sina", "custom")
        ]

        for path in sender_paths:
            with self.subTest(provider=path.parent.name):
                source = path.read_text(encoding="utf-8")
                self.assertIn("build_alternative_body", source)
                self.assertRegex(
                    source,
                    re.compile(
                        r"async def send_message\([\s\S]*?inline_images[\s\S]*?\) -> SendResult:",
                    ),
                )
                self.assertRegex(
                    source,
                    re.compile(
                        r"def _send_sync\([\s\S]*?inline_images[\s\S]*?\):",
                    ),
                )
                self.assertIn("build_alternative_body(body_html, body_text, inline_images)", source)

    def test_base_sender_interface_exposes_optional_inline_images(self):
        backend_root = Path(__file__).resolve().parents[1]
        source = (backend_root / "providers" / "base.py").read_text(encoding="utf-8")

        self.assertRegex(
            source,
            re.compile(r"async def send_message\([\s\S]*?inline_images:.*?= None,[\s\S]*?\) -> SendResult:"),
        )
        self.assertIn("inline_images: 随邮件发送的 CID 内嵌图片", source)


if __name__ == "__main__":
    unittest.main()
