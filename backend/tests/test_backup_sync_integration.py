import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

from services import mail_cache


class BackupSyncIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_new_uids_schedule_one_batch_archive_when_enabled(self):
        account = types.SimpleNamespace(id="account-1", user_uid="user-1", email="user@example.com")
        archive_batch = AsyncMock(return_value=2)
        task_factory = Mock()
        task_factory.side_effect = lambda coro, **_kwargs: coro.close()

        with (
            patch("services.backup.should_archive", new=AsyncMock(return_value=True)),
            patch("services.backup.archive_messages_batch", new=archive_batch),
            patch("services.mail_cache.create_background_task", task_factory),
        ):
            await mail_cache.schedule_archive_for_new_uids(account, "INBOX", [42, 43, 42])

        archive_batch.assert_called_once_with(account, "INBOX", [42, 43])
        task_factory.assert_called_once()
        self.assertEqual(task_factory.call_args.kwargs["name"], "archive_batch_account-1_INBOX")

    async def test_new_uids_do_not_schedule_archive_when_disabled(self):
        account = types.SimpleNamespace(id="account-1", user_uid="user-1", email="user@example.com")
        task_factory = Mock()

        with (
            patch("services.backup.should_archive", new=AsyncMock(return_value=False)),
            patch("services.mail_cache.create_background_task", task_factory),
        ):
            await mail_cache.schedule_archive_for_new_uids(account, "INBOX", [42])

        task_factory.assert_not_called()

    async def test_deleted_remote_uids_mark_archive_before_cache_purge(self):
        mark_deleted = AsyncMock(return_value=2)
        with patch("services.backup.mark_archived_as_deleted", new=mark_deleted):
            deleted = await mail_cache.mark_archived_deleted_before_purge(
                "account-1",
                "INBOX",
                cached_uids={40, 41, 42},
                remote_uids={42},
            )

        self.assertEqual(deleted, {40, 41})
        mark_deleted.assert_awaited_once_with("account-1", "INBOX", [40, 41])


if __name__ == "__main__":
    unittest.main()
