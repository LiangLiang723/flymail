import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from errors import AppError
from models import Account
from providers.base import Attachment
from routes import messages
from services import attachment_cache


class AttachmentDownloadPolicyTest(unittest.IsolatedAsyncioTestCase):
    async def test_local_object_hit_returns_without_remote_connect(self):
        cached = {
            "account_id": "account-1",
            "user_uid": "user-1",
            "uid": 10,
            "folder": "INBOX",
            "part_number": 1,
            "filename": "report.pdf",
            "content_type": "application/pdf",
            "size": 4,
            "content_id": "",
            "is_inline": False,
            "local_path": "/objects/hash",
            "content_sha256": "a" * 64,
            "last_accessed_at": 1,
        }
        account = Account(
            id="account-1",
            user_uid="user-1",
            email="user@example.com",
            provider="gmail",
            status="active",
        )
        with (
            patch("routes.messages._get_account", new=AsyncMock(return_value=("user-1", account))),
            patch("routes.messages.get_cached_attachment", new=AsyncMock(return_value=cached)),
            patch("routes.messages.resolve_cached_attachment_path", new=AsyncMock(return_value=Path("/objects/hash"))),
            patch("routes.messages.ProviderFactory") as factory,
        ):
            response = await messages.download_attachment(object(), "10", 1, "INBOX", "account-1")

        self.assertEqual(response.path, "/objects/hash")
        factory.get_receiver.assert_not_called()

    async def test_offline_without_valid_cache_keeps_existing_error(self):
        account = Account(
            id="account-1",
            user_uid="user-1",
            email="user@example.com",
            provider="gmail",
            status="offline",
        )
        with (
            patch("routes.messages._get_account", new=AsyncMock(return_value=("user-1", account))),
            patch("routes.messages.get_cached_attachment", new=AsyncMock(return_value=None)),
        ):
            with self.assertRaises(AppError) as raised:
                await messages.download_attachment(object(), "10", 1, "INBOX", "account-1")
        self.assertEqual(raised.exception.code, 404)

    async def test_oversized_attachment_uses_transient_download(self):
        account = Account(
            id="account-1",
            user_uid="user-1",
            email="user@example.com",
            provider="gmail",
            status="active",
        )
        attachment = Attachment(
            filename="large.bin",
            content_type="application/octet-stream",
            size=101,
            part_number=2,
        )
        with tempfile.TemporaryDirectory() as tmp:
            transient = Path(tmp) / "large.download"
            transient.write_bytes(b"data")
            with (
                patch("routes.messages.should_persist_normal_attachment", new=AsyncMock(return_value=False)),
                patch("routes.messages.write_transient_download", return_value=transient),
                patch("routes.messages.cache_attachment_bytes", new=AsyncMock()) as cache_bytes,
            ):
                result = await messages._persist_attachment_locally(
                    account=account,
                    user_uid="user-1",
                    folder="INBOX",
                    uid_num=10,
                    message_date="2026-07-30T10:00:00Z",
                    attachment=attachment,
                    data=b"data",
                )

        self.assertTrue(result.transient)
        self.assertEqual(result.path, str(transient))
        cache_bytes.assert_not_awaited()

    async def test_other_users_account_is_rejected_before_object_resolution(self):
        own_account = types.SimpleNamespace(id="account-1", status="active")
        resolver = AsyncMock()
        with (
            patch("routes.messages.get_uid", new=AsyncMock(return_value="user-1")),
            patch("routes.messages.get_accounts", new=AsyncMock(return_value=[own_account])),
            patch("routes.messages.resolve_cached_attachment_path", resolver),
        ):
            with self.assertRaises(AppError):
                await messages.download_attachment(object(), "10", 1, "INBOX", "account-2")
        resolver.assert_not_awaited()


class AttachmentReferenceCleanupTest(unittest.IsolatedAsyncioTestCase):
    async def test_delete_wrapper_releases_only_affected_hashes(self):
        hashes = {"a" * 64, "b" * 64}
        with (
            patch("services.attachment_cache.list_attachment_hashes_for_messages", new=AsyncMock(return_value=hashes)),
            patch("services.attachment_cache.delete_cached_message", new=AsyncMock(return_value=True)),
            patch("services.attachment_cache.release_unreferenced_objects", new=AsyncMock()) as release,
        ):
            deleted = await attachment_cache.delete_cached_message_and_release("account-1", 10, "INBOX")
        self.assertTrue(deleted)
        release.assert_awaited_once_with(hashes)

    async def test_account_clear_releases_shared_objects_after_rows_are_deleted(self):
        hashes = {"a" * 64}
        with (
            patch("services.attachment_cache.list_attachment_hashes_for_messages", new=AsyncMock(return_value=hashes)),
            patch("services.attachment_cache.delete_cached_messages_by_account", new=AsyncMock(return_value=5)),
            patch("services.attachment_cache.delete_cached_attachments_by_account", new=AsyncMock(return_value=3)),
            patch("services.attachment_cache.release_unreferenced_objects", new=AsyncMock()) as release,
        ):
            result = await attachment_cache.clear_account_cache_and_release("account-1")

        self.assertEqual(result, (5, 3))
        release.assert_awaited_once_with(hashes)


if __name__ == "__main__":
    unittest.main()
