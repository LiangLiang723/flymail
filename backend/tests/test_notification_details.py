import json
import types
import unittest
from unittest.mock import AsyncMock, patch

from services.sync import MailSyncService, build_notification_preview


class NotificationDetailsTest(unittest.IsolatedAsyncioTestCase):
    def test_notification_preview_strips_html_and_limits_length(self):
        preview = build_notification_preview("", "<p>Hello <strong>world</strong></p>" + ("x" * 1200))

        self.assertNotIn("<", preview)
        self.assertTrue(preview.startswith("Hello world"))
        self.assertLessEqual(len(preview), 1000)

    async def test_handle_new_mail_only_notifies_messages_added_after_previous_max_uid(self):
        service = MailSyncService()
        service.notify_clients = AsyncMock()
        service.refresh_clients = AsyncMock()
        account = types.SimpleNamespace(
            id="account-1",
            user_uid="user-1",
            email="user@example.com",
            provider="custom",
        )
        cached_rows = {
            "messages": [
                {"uid": 43, "id": "43", "folder": "INBOX"},
                {"uid": 42, "id": "42", "folder": "INBOX"},
                {"uid": 41, "id": "41", "folder": "INBOX"},
            ]
        }
        detail_42 = {
            "uid": 42,
            "subject": "new 42",
            "from_addr": "from@example.com",
            "to_addr": "user@example.com",
            "cc": "",
            "date": "2026-07-28T10:00:00Z",
            "body_text": "preview 42",
            "body_html": "",
            "has_attachments": False,
            "message_id": "<42@example.com>",
            "folder": "INBOX",
        }
        detail_43 = {**detail_42, "uid": 43, "subject": "new 43", "body_text": "preview 43", "message_id": "<43@example.com>"}

        with (
            patch("services.sync.get_max_cached_uid", new=AsyncMock(return_value=41)),
            patch("services.mail_cache.sync_recent_folder_to_cache", new=AsyncMock(return_value=2)),
            patch("services.sync.get_cached_messages_by_folder", new=AsyncMock(return_value=cached_rows)),
            patch("services.sync.get_cached_message_detail", new=AsyncMock(side_effect=[detail_43, detail_42])),
            patch("services.sync.build_cached_message_id", side_effect=lambda account_id, folder, uid: f"{account_id}:{folder}:{uid}"),
        ):
            await service._handle_new_mail(account, "INBOX")

        items = service.notify_clients.await_args.kwargs["items"]
        self.assertEqual([item["uid"] for item in items], [43, 42])
        self.assertEqual(items[0]["message_cache_id"], "account-1:INBOX:43")
        self.assertEqual(items[0]["body_preview"], "preview 43")
        service.refresh_clients.assert_awaited_once_with("account-1", "INBOX", user_uid="user-1")

    async def test_new_mail_notification_persists_details_and_dispatches_external_event(self):
        service = MailSyncService()
        service._broadcast = AsyncMock()
        item = {
            "message_cache_id": "account-1:INBOX:42",
            "uid": 42,
            "subject": "主题",
            "from_addr": "Alice <alice@example.com>",
            "to_addr": "user@example.com",
            "cc": "",
            "mail_date": "2026-07-28T10:00:00Z",
            "body_preview": "正文预览",
            "has_attachments": True,
            "rfc_message_id": "<message-42@example.com>",
        }

        with (
            patch("services.sync.create_notification", new=AsyncMock()) as create_notification,
            patch("services.notification_dispatch.dispatch", new=AsyncMock()) as dispatch,
        ):
            await service.notify_clients(
                "account-1",
                "INBOX",
                provider="custom",
                email="user@example.com",
                user_uid="user-1",
                items=[item],
            )

        notification = create_notification.await_args.args[0]
        self.assertEqual(notification.message_cache_id, item["message_cache_id"])
        self.assertEqual(notification.message_uid, 42)
        self.assertEqual(notification.subject, "主题")
        self.assertEqual(notification.body_preview, "正文预览")
        self.assertTrue(notification.has_attachments)

        payload = json.loads(service._broadcast.await_args.args[0])
        self.assertEqual(payload["message_cache_id"], item["message_cache_id"])
        self.assertEqual(payload["message_uid"], 42)
        self.assertEqual(payload["subject"], "主题")
        dispatch.assert_awaited_once()
        self.assertEqual(dispatch.await_args.args[0]["user_uid"], "user-1")


if __name__ == "__main__":
    unittest.main()
