import json
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

from routes import settings


class GmailProxySettingsTest(unittest.IsolatedAsyncioTestCase):
    async def test_notification_channels_read_proxy_from_current_user_settings(self):
        from services import settings as settings_service

        with patch(
            "db.get_user_settings",
            new=AsyncMock(return_value={
                "gmail_proxy_enabled": True,
                "gmail_proxy_url": "http://proxy.test:8080",
            }),
        ) as get_settings:
            result = await settings_service.get_gmail_proxy_settings("user-1")

        get_settings.assert_awaited_once_with(
            "user-1",
            ["gmail_proxy_enabled", "gmail_proxy_url"],
        )
        self.assertEqual(result["gmail_proxy_url"], "http://proxy.test:8080")

    def test_oauth_credentials_inherit_current_users_proxy_settings(self):
        from providers.base import Credentials
        from routes.auth import merge_gmail_proxy_settings

        credentials = Credentials(
            provider_type="gmail",
            access_token="access",
            refresh_token="refresh",
            extra={"email": "user@gmail.com"},
        )

        merged = merge_gmail_proxy_settings(
            credentials,
            {"gmail_proxy_enabled": True, "gmail_proxy_url": " http://proxy.test:8080 "},
        )

        self.assertIs(merged, credentials)
        self.assertTrue(credentials.extra["gmail_proxy_enabled"])
        self.assertEqual(credentials.extra["gmail_proxy_url"], "http://proxy.test:8080")

    async def test_apply_proxy_updates_only_current_users_gmail_accounts(self):
        gmail = types.SimpleNamespace(
            id="gmail-1",
            provider="gmail",
            credentials_json=json.dumps({
                "access_token": "token",
                "refresh_token": "refresh",
                "expires_at": 123,
                "extra": {"email": "user@gmail.com"},
            }),
        )
        outlook = types.SimpleNamespace(
            id="outlook-1",
            provider="outlook",
            credentials_json=json.dumps({"extra": {"email": "user@outlook.com"}}),
        )
        update = AsyncMock(return_value=True)
        add_account = AsyncMock()

        with (
            patch("routes.settings.get_accounts", new=AsyncMock(return_value=[gmail, outlook])) as get_accounts,
            patch("routes.settings.update_account_credentials", new=update),
            patch("routes.settings.sync_service", types.SimpleNamespace(add_account=add_account)),
            patch("routes.settings.create_background_task") as create_task,
        ):
            create_task.side_effect = lambda coro, **_kwargs: coro.close()
            await settings.apply_user_gmail_proxy("user-1", True, "http://proxy.test:8080")

        get_accounts.assert_awaited_once_with("user-1")
        update.assert_awaited_once()
        saved = json.loads(update.await_args.args[1])
        self.assertEqual(saved["access_token"], "token")
        self.assertTrue(saved["extra"]["gmail_proxy_enabled"])
        self.assertEqual(saved["extra"]["gmail_proxy_url"], "http://proxy.test:8080")
        create_task.assert_called_once()
        self.assertEqual(create_task.call_args.kwargs["name"], "reload_gmail_proxy")

    def test_proxy_test_rejects_credential_leak_in_error(self):
        with patch("routes.settings.create_proxy_socket", side_effect=ConnectionError("proxy secret failure")):
            result = settings._test_proxy_to_google_sync("http://user:secret@proxy.test:8080")

        self.assertFalse(result["success"])
        self.assertNotIn("user", result["message"])
        self.assertNotIn("secret", result["message"])


if __name__ == "__main__":
    unittest.main()
