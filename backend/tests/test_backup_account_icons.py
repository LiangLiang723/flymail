import unittest
from unittest.mock import AsyncMock, patch

from models import Account
from routes import backup


class BackupAccountIconTest(unittest.IsolatedAsyncioTestCase):
    async def test_backup_settings_include_safe_account_icon_fields(self):
        account = Account(
            id="account-1",
            user_uid="user-1",
            email="user@example.com",
            provider="custom",
            icon_type="preset",
            icon_value="work",
            updated_at=123.0,
        )
        with (
            patch.object(backup, "get_uid", AsyncMock(return_value="user-1")),
            patch.object(backup, "get_user_setting", AsyncMock(side_effect=[False, [], ""])),
            patch.object(backup, "get_accounts", AsyncMock(return_value=[account])),
            patch.object(backup, "get_available_backup_dirs", AsyncMock(return_value=[])),
        ):
            payload = await backup.get_backup_settings(object())

        item = payload["accounts"][0]
        self.assertEqual(item["icon_type"], "preset")
        self.assertEqual(item["icon_value"], "work")
        self.assertEqual(item["icon_url"], "")
        self.assertNotIn("/data/", repr(item))

    async def test_backup_status_include_safe_account_icon_fields(self):
        account = Account(
            id="account-1",
            user_uid="user-1",
            email="user@example.com",
            provider="custom",
            icon_type="preset",
            icon_value="work",
            updated_at=123.0,
        )
        with (
            patch.object(backup, "get_uid", AsyncMock(return_value="user-1")),
            patch.object(backup, "get_archive_stats", AsyncMock(return_value={"total": 0, "deleted": 0, "last_archived": 0, "accounts": []})),
            patch.object(backup, "get_user_setting", AsyncMock(return_value=["account-1"])),
            patch.object(backup, "get_accounts", AsyncMock(return_value=[account])),
        ):
            payload = await backup.get_backup_status(object())

        item = payload["accounts"][0]
        self.assertEqual(item["icon_type"], "preset")
        self.assertEqual(item["icon_value"], "work")
        self.assertEqual(item["icon_url"], "")


if __name__ == "__main__":
    unittest.main()
