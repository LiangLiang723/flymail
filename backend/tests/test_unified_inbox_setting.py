import types
import unittest
from unittest.mock import AsyncMock, patch

from routes import messages, settings
from schemas import UnifiedSettingsRequest, UnifiedSettingsResponse


class UnifiedInboxSettingTest(unittest.IsolatedAsyncioTestCase):
    def test_unified_setting_models_default_to_disabled(self):
        request = UnifiedSettingsRequest()
        response = UnifiedSettingsResponse(account_ids=[], accounts=[])

        self.assertIsNone(request.account_ids)
        self.assertIsNone(request.enabled)
        self.assertFalse(response.enabled)

    async def test_get_unified_settings_defaults_to_disabled(self):
        with (
            patch.object(settings, "get_uid", AsyncMock(return_value="user-1")),
            patch.object(settings, "get_accounts", AsyncMock(return_value=[])),
            patch("db.get_user_settings", AsyncMock(return_value={})),
        ):
            payload = await settings.get_unified_settings(object())

        self.assertFalse(payload["enabled"])
        self.assertEqual(payload["account_ids"], [])

    async def test_saving_enabled_state_preserves_account_selection(self):
        set_settings = AsyncMock()
        with (
            patch.object(settings, "get_uid", AsyncMock(return_value="user-1")),
            patch.object(settings, "get_accounts", AsyncMock(return_value=[])),
            patch("db.get_user_settings", AsyncMock(return_value={"unified_account_ids": ["account-1"]})),
            patch("db.set_user_settings", set_settings),
        ):
            result = await settings.save_unified_settings(
                object(),
                UnifiedSettingsRequest(enabled=True),
            )

        set_settings.assert_awaited_once_with("user-1", {"unified_inbox_enabled": True})
        self.assertEqual(result, {"success": True})

    async def test_disabled_unified_inbox_does_not_query_messages(self):
        account = types.SimpleNamespace(id="account-1", email="user@example.com", provider="custom")
        query_messages = AsyncMock(return_value={
            "messages": [],
            "total": 0,
            "unread_total": 0,
            "page": 1,
            "page_size": 40,
        })
        with (
            patch.object(messages, "get_uid", AsyncMock(return_value="user-1")),
            patch.object(messages, "get_accounts", AsyncMock(return_value=[account])),
            patch.object(
                messages,
                "get_user_settings",
                AsyncMock(return_value={
                    "unified_inbox_enabled": False,
                    "unified_account_ids": ["account-1"],
                }),
            ),
            patch.object(messages, "get_unified_inbox_messages", query_messages),
            patch.object(messages, "get_unified_inbox_stats", AsyncMock(return_value={"total_count": 0, "unread_count": 0})),
            patch.object(messages, "get_unified_inbox_filter_counts", AsyncMock(return_value={})),
        ):
            payload = await messages.list_unified_messages(object())

        query_messages.assert_not_awaited()
        self.assertTrue(payload["no_accounts"])
        self.assertEqual(payload["messages"], [])


if __name__ == "__main__":
    unittest.main()
