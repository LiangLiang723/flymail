import unittest
from unittest.mock import AsyncMock, patch

from models import Account
from routes import settings


class UnifiedAccountIconTest(unittest.IsolatedAsyncioTestCase):
    async def test_unified_settings_include_safe_account_icon_fields(self):
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
            patch.object(settings, "get_uid", AsyncMock(return_value="user-1")),
            patch.object(settings, "get_accounts", AsyncMock(return_value=[account])),
            patch("db.get_user_settings", AsyncMock(return_value={"unified_account_ids": ["account-1"]})),
        ):
            payload = await settings.get_unified_settings(object())

        item = payload["accounts"][0]
        self.assertEqual(item["icon_type"], "preset")
        self.assertEqual(item["icon_value"], "work")
        self.assertEqual(item["icon_url"], "")
        self.assertNotIn("/data/", repr(item))


if __name__ == "__main__":
    unittest.main()
