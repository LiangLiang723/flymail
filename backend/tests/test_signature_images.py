import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image


class SignatureImageStorageTest(unittest.TestCase):
    def _png_bytes(self, size=(2400, 1200), mode="RGBA") -> bytes:
        image = Image.new(mode, size, (30, 120, 220, 180) if mode == "RGBA" else "blue")
        payload = io.BytesIO()
        image.save(payload, format="PNG")
        return payload.getvalue()

    def test_uploaded_image_is_normalized_and_stored_in_user_bucket(self):
        from services import signature_images

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "signature-images"
            with patch.object(signature_images, "SIGNATURE_IMAGES_DIR", root):
                stored = signature_images.save_signature_image("user-1", self._png_bytes())
                resolved = signature_images.resolve_signature_image(stored.image_id)

            self.assertEqual(resolved, stored.path.resolve())
            self.assertEqual(stored.path.suffix, ".webp")
            self.assertEqual(stored.path.parent.parent, root)
            self.assertNotIn("user-1", stored.image_id)
            with Image.open(stored.path) as normalized:
                self.assertEqual(normalized.format, "WEBP")
                self.assertLessEqual(max(normalized.size), 1200)
                self.assertIn("A", normalized.getbands())

    def test_invalid_image_is_rejected_without_leaving_files(self):
        from services import signature_images

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "signature-images"
            with patch.object(signature_images, "SIGNATURE_IMAGES_DIR", root):
                with self.assertRaisesRegex(ValueError, "无法读取该图片"):
                    signature_images.save_signature_image("user-1", b"not-an-image")

            self.assertFalse(any(root.rglob("*")) if root.exists() else False)

    def test_route_declares_upload_and_public_image_delivery(self):
        backend_root = Path(__file__).resolve().parents[1]
        route_source = (backend_root / "routes" / "signatures.py").read_text(encoding="utf-8")
        data_paths_source = (backend_root / "data_paths.py").read_text(encoding="utf-8")

        self.assertIn('"/api/signatures/images"', route_source)
        self.assertIn('"/api/signature-images/{image_id}"', route_source)
        self.assertIn("save_signature_image", route_source)
        self.assertIn("resolve_signature_image", route_source)
        self.assertIn("SIGNATURE_IMAGES_DIR", data_paths_source)


if __name__ == "__main__":
    unittest.main()
