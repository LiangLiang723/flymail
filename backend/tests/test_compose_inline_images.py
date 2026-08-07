import re
import unittest
from pathlib import Path


class ComposeInlineImageContractTest(unittest.TestCase):
    def test_compose_prepares_inline_images_for_send_and_draft_but_not_schedule_storage(self):
        source = (Path(__file__).resolve().parents[1] / "routes" / "compose.py").read_text(encoding="utf-8")

        self.assertIn("from services.inline_images import prepare_inline_images", source)
        self.assertRegex(
            source,
            re.compile(r"prepared_body\s*=\s*await prepare_inline_images\(user_uid, body_html\)"),
        )
        self.assertIn("body_html=prepared_body.body_html", source)
        self.assertIn("inline_images=prepared_body.inline_images", source)
        self.assertRegex(
            source,
            re.compile(r"schedule_email\([\s\S]*?body_html=body_html,"),
        )

    def test_legacy_send_endpoint_prepares_inline_images(self):
        source = (Path(__file__).resolve().parents[1] / "routes" / "compose.py").read_text(encoding="utf-8")
        legacy_section = source.split('@router.post("/api/messages/send"', 1)[1]

        self.assertIn("await prepare_inline_images", legacy_section)
        self.assertIn("inline_images=prepared_body.inline_images", legacy_section)


if __name__ == "__main__":
    unittest.main()
