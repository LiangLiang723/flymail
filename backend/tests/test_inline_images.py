import asyncio
import base64
import hashlib
import importlib
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class InlineImagePreparationTest(unittest.TestCase):
    def _load_module(self):
        spec = importlib.util.find_spec("services.inline_images")
        self.assertIsNotNone(spec, "services.inline_images must exist")
        return importlib.import_module("services.inline_images")

    def _owned_image(self, root: Path, user_uid: str = "user-1"):
        from services import signature_images

        raw = b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"managed-image-bytes"
        bucket = hashlib.sha256(user_uid.encode("utf-8")).hexdigest()[:24]
        image_id = f"{bucket}.{'b' * 32}"
        path = root / bucket / f"{'b' * 32}.webp"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return signature_images, image_id, raw

    def test_managed_image_becomes_cid_and_repeated_reference_is_deduplicated(self):
        inline_images = self._load_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "signature-images"
            signature_images, image_id, raw = self._owned_image(root)
            html = (
                f'<p>A</p><img src="flymail-signature-image:{image_id}" '
                f'data-flymail-signature-image="{image_id}" width="367">'
                f'<img src="flymail-signature-image:{image_id}" data-flymail-signature-image="{image_id}">'
            )
            with patch.object(signature_images, "SIGNATURE_IMAGES_DIR", root):
                prepared = asyncio.run(inline_images.prepare_inline_images("user-1", html))

        self.assertNotIn("flymail-signature-image:", prepared.body_html)
        self.assertNotIn("data-flymail-signature-image", prepared.body_html)
        self.assertNotIn("/api/signature-images/", prepared.body_html)
        self.assertEqual(prepared.body_html.count('src="cid:'), 2)
        self.assertIn('width="367"', prepared.body_html)
        self.assertEqual(len(prepared.inline_images), 1)
        self.assertEqual(prepared.inline_images[0].data, raw)
        self.assertEqual(prepared.body_html.count(prepared.inline_images[0].content_id), 2)
        self.assertEqual(prepared.inline_images[0].content_type, "image/webp")

    def test_wrong_user_and_missing_managed_image_are_rejected(self):
        inline_images = self._load_module()
        from services import signature_images

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "signature-images"
            _signature_images, image_id, _raw = self._owned_image(root)
            html = f'<img src="flymail-signature-image:{image_id}" data-flymail-signature-image="{image_id}">'
            with patch.object(signature_images, "SIGNATURE_IMAGES_DIR", root):
                with self.assertRaisesRegex(ValueError, "不属于当前用户"):
                    asyncio.run(inline_images.prepare_inline_images("user-2", html))

            missing_id = f"{hashlib.sha256(b'user-1').hexdigest()[:24]}.{'c' * 32}"
            missing_html = f'<img src="flymail-signature-image:{missing_id}">'
            with patch.object(signature_images, "SIGNATURE_IMAGES_DIR", root):
                with self.assertRaisesRegex(ValueError, "不存在"):
                    asyncio.run(inline_images.prepare_inline_images("user-1", missing_html))

    def test_data_uri_image_becomes_inline_part(self):
        inline_images = self._load_module()
        raw = b"\x89PNG\r\n\x1a\nclipboard-image"
        data_uri = "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
        prepared = asyncio.run(
            inline_images.prepare_inline_images("user-1", f'<p>x</p><img src="{data_uri}" width="120">')
        )

        self.assertIn('src="cid:', prepared.body_html)
        self.assertNotIn("data:image/png", prepared.body_html)
        self.assertIn('width="120"', prepared.body_html)
        self.assertEqual(len(prepared.inline_images), 1)
        self.assertEqual(prepared.inline_images[0].data, raw)
        self.assertEqual(prepared.inline_images[0].content_type, "image/png")

    def test_arbitrary_remote_image_is_left_untouched(self):
        inline_images = self._load_module()
        html = '<p>x</p><img src="https://cdn.example.com/logo.png" width="200">'
        prepared = asyncio.run(inline_images.prepare_inline_images("user-1", html))

        self.assertEqual(prepared.body_html, html)
        self.assertEqual(prepared.inline_images, [])


if __name__ == "__main__":
    unittest.main()
