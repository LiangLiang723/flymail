import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from models import User
from routes.local_auth import _user_payload


class UserPayloadTest(unittest.TestCase):
    def test_existing_user_with_null_avatar_path_remains_loadable(self):
        user = User(
            id="legacy-user",
            username="legacy",
            nickname="",
            avatar_path=None,
            password_hash="hash",
            role="user",
            status="active",
        )
        payload = _user_payload(user)
        self.assertEqual(payload["avatar_url"], "")
        self.assertEqual(payload["display_name"], "legacy")

    def test_authenticated_payload_exposes_nickname_and_versioned_avatar_url(self):
        user = User(
            id="user-1",
            username="liangliang",
            nickname="亮亮",
            avatar_path="/data/flymail/files/avatars/user-1.webp",
            password_hash="hash",
            role="user",
            status="active",
            updated_at=123.5,
        )

        payload = _user_payload(user)

        self.assertEqual(payload["nickname"], "亮亮")
        self.assertEqual(payload["display_name"], "亮亮")
        self.assertEqual(payload["avatar_url"], "/api/auth/avatar/user-1?v=123")


class AvatarStorageTest(unittest.TestCase):
    def _load_service(self):
        service_path = BACKEND_DIR / "services" / "user_profiles.py"
        self.assertTrue(service_path.exists(), "user profile avatar service should exist")
        spec = importlib.util.spec_from_file_location("test_user_profiles_service", service_path)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        return module

    def test_avatar_is_normalized_to_bounded_webp_inside_avatar_directory(self):
        service = self._load_service()
        source = Image.new("RGB", (900, 450), "white")
        payload = io.BytesIO()
        source.save(payload, format="PNG")

        with tempfile.TemporaryDirectory() as temp_dir:
            avatar_dir = Path(temp_dir) / "avatars"
            with patch.object(service, "AVATARS_DIR", avatar_dir):
                stored_path = Path(service.save_user_avatar("user-1", payload.getvalue()))

            self.assertEqual(stored_path.parent, avatar_dir)
            self.assertEqual(stored_path.suffix, ".webp")
            self.assertTrue(stored_path.exists())
            with Image.open(stored_path) as normalized:
                self.assertEqual(normalized.size, (256, 256))
                self.assertEqual(normalized.format, "WEBP")

    def test_invalid_avatar_bytes_are_rejected_without_creating_a_file(self):
        service = self._load_service()
        with tempfile.TemporaryDirectory() as temp_dir:
            avatar_dir = Path(temp_dir) / "avatars"
            with patch.object(service, "AVATARS_DIR", avatar_dir):
                with self.assertRaises(ValueError):
                    service.save_user_avatar("user-1", b"not-an-image")
            self.assertFalse(any(avatar_dir.glob("*")) if avatar_dir.exists() else False)

    def test_avatar_resolution_rejects_files_outside_avatar_directory(self):
        service = self._load_service()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            avatar_dir = root / "avatars"
            avatar_dir.mkdir()
            allowed = avatar_dir / "user-1.webp"
            allowed.write_bytes(b"avatar")
            outside = root / "outside.webp"
            outside.write_bytes(b"outside")
            with patch.object(service, "AVATARS_DIR", avatar_dir):
                self.assertEqual(service.resolve_user_avatar(str(allowed)), allowed.resolve())
                self.assertIsNone(service.resolve_user_avatar(str(outside)))


if __name__ == "__main__":
    unittest.main()
