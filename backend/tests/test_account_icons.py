import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from PIL import Image


class AccountIconStorageTest(unittest.TestCase):
    def _image_bytes(self, fmt="PNG", size=(600, 300), color=(20, 120, 220)):
        buffer = io.BytesIO()
        Image.new("RGB", size, color).save(buffer, format=fmt)
        return buffer.getvalue()

    def test_upload_is_normalized_to_256_webp(self):
        from services import account_icons

        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            account_icons, "ACCOUNT_ICONS_DIR", Path(temp_dir)
        ):
            target = account_icons.save_account_icon("user-1", "account-1", self._image_bytes())
            self.assertEqual(target, Path(temp_dir) / "user-1" / "account-1.webp")
            with Image.open(target) as result:
                self.assertEqual(result.size, (256, 256))
                self.assertEqual(result.format, "WEBP")

    def test_invalid_or_unsupported_images_are_rejected(self):
        from services import account_icons

        gif = self._image_bytes(fmt="GIF")
        with self.assertRaisesRegex(ValueError, "仅支持 JPG、PNG 或 WebP 图片"):
            account_icons.save_account_icon("user-1", "account-1", gif)
        with self.assertRaisesRegex(ValueError, "无法读取该图片"):
            account_icons.save_account_icon("user-1", "account-1", b"not-an-image")

    def test_failed_replacement_keeps_existing_file(self):
        from services import account_icons

        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            account_icons, "ACCOUNT_ICONS_DIR", Path(temp_dir)
        ):
            target = account_icons.save_account_icon("user-1", "account-1", self._image_bytes())
            before = target.read_bytes()
            with self.assertRaises(ValueError):
                account_icons.save_account_icon("user-1", "account-1", b"broken")
            self.assertEqual(target.read_bytes(), before)

    def test_resolution_rejects_identifiers_outside_safe_path(self):
        from services import account_icons

        with self.assertRaises(ValueError):
            account_icons.save_account_icon("../user", "account-1", self._image_bytes())
        with self.assertRaises(ValueError):
            account_icons.save_account_icon("user-1", "../account", self._image_bytes())


class AccountIconCleanupContractTest(unittest.TestCase):
    def test_account_deletion_removes_icon_only_after_database_delete(self):
        source = (Path(__file__).resolve().parents[1] / "services" / "history_sync.py").read_text(encoding="utf-8")
        delete_call = "deleted = await delete_account(account.id, account.user_uid)"
        cleanup_call = "if deleted:\n            delete_account_icon(account.user_uid, account.id)"
        self.assertIn(delete_call, source)
        self.assertIn(cleanup_call, source)
        self.assertLess(source.index(delete_call), source.index(cleanup_call))


class AccountIconRouteTest(unittest.IsolatedAsyncioTestCase):
    async def test_other_users_account_is_hidden_by_preset_endpoint(self):
        from errors import AppError
        from models import Account
        from routes import accounts
        from schemas import AccountIconPresetRequest

        own = Account(id="own", user_uid="user-1", email="own@example.com", provider="custom")
        with patch.object(accounts, "get_uid", AsyncMock(return_value="user-1")), patch.object(
            accounts, "get_accounts", AsyncMock(return_value=[own])
        ):
            with self.assertRaises(AppError) as raised:
                await accounts.set_account_icon_preset(
                    "other",
                    request=object(),
                    body=AccountIconPresetRequest(preset_id="work"),
                )
        self.assertEqual(raised.exception.code, 404)

    async def test_account_payload_never_returns_absolute_icon_path(self):
        from models import Account
        from services.account_presenter import account_icon_fields

        account = Account(
            id="account-1",
            user_uid="user-1",
            email="a@example.com",
            provider="custom",
            icon_type="upload",
            updated_at=123.9,
        )
        with patch("services.account_presenter.resolve_account_icon", return_value=Path("/data/flymail/files/account-icons/user-1/account-1.webp")):
            with patch.object(Path, "is_file", return_value=True):
                payload = account_icon_fields(account)
        self.assertEqual(payload["icon_url"], "/api/accounts/account-1/icon?v=123")
        self.assertNotIn("/data/", repr(payload))


if __name__ == "__main__":
    unittest.main()
