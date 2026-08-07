import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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

    def test_internal_signature_image_reference_round_trips_and_checks_owner(self):
        from services import signature_images

        bucket = hashlib.sha256(b"user-1").hexdigest()[:24]
        image_id = f"{bucket}.{'b' * 32}"
        reference = signature_images.signature_image_reference(image_id)

        self.assertEqual(reference, f"flymail-signature-image:{image_id}")
        self.assertEqual(signature_images.parse_signature_image_id(reference), image_id)
        self.assertEqual(
            signature_images.parse_signature_image_id(
                f"https://mail.example/mail/api/signature-images/{image_id}"
            ),
            image_id,
        )
        self.assertTrue(signature_images.signature_image_belongs_to_user("user-1", image_id))
        self.assertFalse(signature_images.signature_image_belongs_to_user("user-2", image_id))
        self.assertIsNone(signature_images.parse_signature_image_id("https://example.com/image.webp"))

    def test_upload_schema_exposes_internal_image_id(self):
        from schemas import SignatureImageUploadResponse

        self.assertIn("image_id", SignatureImageUploadResponse.model_fields)
        self.assertIn("url", SignatureImageUploadResponse.model_fields)

    def test_legacy_attachment_image_url_parser_accepts_owned_flymail_routes_only(self):
        from services.signature_images import parse_legacy_attachment_image_url

        parsed = parse_legacy_attachment_image_url(
            "http://old-host:36080/api/messages/account_INBOX_4942/attachments/4?account_id=a1&folder=INBOX"
        )
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.account_id, "a1")
        self.assertEqual(parsed.uid, 4942)
        self.assertEqual(parsed.part_number, 4)
        self.assertEqual(parsed.folder, "INBOX")

        relative = parse_legacy_attachment_image_url(
            "/api/messages/4942/attachments/4?account_id=a1&folder=Sent"
        )
        self.assertIsNotNone(relative)
        self.assertEqual(relative.uid, 4942)
        self.assertEqual(relative.folder, "Sent")

        self.assertIsNone(parse_legacy_attachment_image_url("https://example.com/image.png"))
        self.assertIsNone(parse_legacy_attachment_image_url("/api/messages/not-a-uid/attachments/4?account_id=a1"))
        self.assertIsNone(parse_legacy_attachment_image_url("/api/messages/4942/attachments/4"))

    def test_legacy_promotion_copies_cached_image_and_preserves_width(self):
        from routes import signatures as signature_routes

        signature = SimpleNamespace(
            id=1,
            name="工作签名",
            content_html=(
                '<p>hello</p><img src="http://old-host:36080/api/messages/4942/attachments/4?'
                'account_id=a1&amp;folder=INBOX" width="367">'
            ),
            is_default=1,
            is_reply_default=0,
            account_id="",
            user_uid="user-1",
        )
        account = SimpleNamespace(id="a1", user_uid="user-1")
        cached = {
            "account_id": "a1",
            "uid": 4942,
            "folder": "INBOX",
            "part_number": 4,
            "content_sha256": "a" * 64,
            "local_path": "/data/object.webp",
        }
        stored = SimpleNamespace(image_id="b" * 24 + "." + "c" * 32)
        request = SimpleNamespace(
            url_for=lambda _name, **kwargs: f"https://mail.example/api/signature-images/{kwargs['image_id']}"
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "legacy.png"
            source.write_bytes(self._png_bytes(size=(32, 16), mode="RGB"))
            with (
                patch.object(signature_routes, "get_account_by_id", AsyncMock(return_value=account)),
                patch.object(signature_routes, "get_cached_attachment", AsyncMock(return_value=cached)),
                patch.object(signature_routes, "resolve_cached_attachment_path", AsyncMock(return_value=source)),
                patch.object(signature_routes, "save_signature_image", return_value=stored) as save_image,
                patch.object(signature_routes, "update_signature", AsyncMock(return_value=True)) as update_signature,
            ):
                changed = __import__("asyncio").run(
                    signature_routes._promote_legacy_signature_images(request, "user-1", signature)
                )

        self.assertTrue(changed)
        self.assertIn(f'src="flymail-signature-image:{stored.image_id}"', signature.content_html)
        self.assertIn(f'data-flymail-signature-image="{stored.image_id}"', signature.content_html)
        self.assertIn('width="367"', signature.content_html)
        self.assertNotIn('/api/messages/4942/attachments/4', signature.content_html)
        save_image.assert_called_once()
        update_signature.assert_awaited_once_with(signature)

    def test_stable_signature_image_url_is_normalized_to_internal_reference(self):
        from routes import signatures as signature_routes

        bucket = hashlib.sha256(b"user-1").hexdigest()[:24]
        image_id = f"{bucket}.{'c' * 32}"
        source = f'<p>x</p><img src="https://mail.example/api/signature-images/{image_id}" width="367">'

        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "managed.webp"
            image_path.write_bytes(b"image")
            with patch.object(signature_routes, "resolve_signature_image", return_value=image_path):
                normalized = signature_routes._normalize_managed_signature_image_html(source, "user-1")

        self.assertIn(f'src="flymail-signature-image:{image_id}"', normalized)
        self.assertIn(f'data-flymail-signature-image="{image_id}"', normalized)
        self.assertIn('width="367"', normalized)

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
