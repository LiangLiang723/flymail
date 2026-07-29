import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


def _load_history_sync_module():
    db_stub = types.ModuleType("db")
    for name in (
        "batch_update_is_read",
        "create_history_sync_job",
        "batch_delete_cached_messages",
        "delete_account",
        "get_cached_attachment",
        "get_cached_attachment_rows",
        "get_cached_count",
        "get_cached_message_detail",
        "get_account_by_id",
        "get_cached_uids",
        "get_history_sync_job",
        "get_history_sync_job_by_id",
        "list_cached_attachments",
        "list_cached_messages_needing_body_check",
        "mark_cached_messages_empty_body_checked",
        "mark_cached_messages_body_checked",
        "update_history_sync_job",
        "upsert_cached_attachments",
        "upsert_cached_messages",
        "delete_cached_attachments_by_account",
        "delete_cached_messages_by_account",
        "delete_folder_stats_by_account",
        "delete_history_sync_jobs_by_account",
        "upsert_folder_stats",
    ):
        setattr(db_stub, name, object())

    data_paths_stub = types.ModuleType("data_paths")
    data_paths_stub.DOWNLOADS_DIR = Path(".")
    data_paths_stub.build_message_file_path = lambda *args, **kwargs: ("", Path("."), False)
    data_paths_stub.coalesce_message_date = lambda *values: next((value for value in values if value), "")
    data_paths_stub.clear_account_storage = lambda *args, **kwargs: None
    data_paths_stub.ensure_message_file_location = lambda *args, **kwargs: ("", Path("."), False)
    data_paths_stub.ensure_data_dirs = lambda: None
    data_paths_stub.normalize_message_date = lambda value, fallback="": value or fallback
    data_paths_stub.UNKNOWN_MESSAGE_DATE = "1970-01-01T00:00:00Z"

    factory_stub = types.ModuleType("providers.factory")
    factory_stub.ProviderFactory = object()

    sync_stub = types.ModuleType("services.sync")
    sync_stub.sync_service = object()

    token_stub = types.ModuleType("services.token")
    token_stub.ensure_token = object()

    logger_stub = types.ModuleType("utils.logger")
    logger_stub.get_logger = lambda name: types.SimpleNamespace(
        debug=lambda *args, **kwargs: None,
        info=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
    )

    tasks_stub = types.ModuleType("utils.tasks")
    tasks_stub.create_background_task = lambda *args, **kwargs: None

    previous = {
        name: sys.modules.get(name)
        for name in (
            "db",
            "data_paths",
            "providers.factory",
            "services.sync",
            "services.token",
            "utils.logger",
            "utils.tasks",
        )
    }
    sys.modules.update(
        {
            "db": db_stub,
            "data_paths": data_paths_stub,
            "providers.factory": factory_stub,
            "services.sync": sync_stub,
            "services.token": token_stub,
            "utils.logger": logger_stub,
            "utils.tasks": tasks_stub,
        }
    )
    try:
        module_path = Path(__file__).resolve().parents[1] / "services" / "history_sync.py"
        spec = importlib.util.spec_from_file_location("history_sync_for_test", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, value in previous.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


class HistorySyncFolderResolutionTest(unittest.TestCase):
    def test_custom_full_sync_keeps_all_remote_folders(self):
        history_sync = _load_history_sync_module()
        remote_folders = [
            types.SimpleNamespace(name="收件箱", path="INBOX"),
            types.SimpleNamespace(name="已发送", path="&XfJT0ZAB-"),
            types.SimpleNamespace(name="OA", path="OA"),
            types.SimpleNamespace(name="ROVO", path="ROVO"),
        ]

        resolved = history_sync._resolve_history_folders(
            remote_folders,
            include_all=True,
        )

        self.assertEqual(
            [folder.path for folder in resolved],
            ["INBOX", "&XfJT0ZAB-", "OA", "ROVO"],
        )

    def test_resolves_netease_sent_folder_by_display_name(self):
        history_sync = _load_history_sync_module()
        remote_folders = [
            types.SimpleNamespace(name="收件箱", path="INBOX"),
            types.SimpleNamespace(name="已发送", path="&XfJT0ZAB-"),
        ]

        resolved = history_sync._resolve_history_folders(remote_folders, ["Sent"])

        self.assertEqual(resolved[0].path, "&XfJT0ZAB-")


class HistorySyncFastRefreshTest(unittest.IsolatedAsyncioTestCase):
    async def test_recent_stage_stops_after_cached_message(self):
        history_sync = _load_history_sync_module()
        account = types.SimpleNamespace(id="account-1", user_uid="user-1", email="a@example.com")
        receiver = AsyncMock()
        receiver.fetch_messages.return_value = types.SimpleNamespace(
            messages=[
                types.SimpleNamespace(uid=105, date="2026-07-03", is_read=False),
                types.SimpleNamespace(uid=104, date="2026-07-03", is_read=False),
                types.SimpleNamespace(uid=103, date="2026-07-03", is_read=False),
            ],
            total=6,
            unread_total=0,
            page_size=50,
        )

        with (
            patch.object(history_sync, "get_cached_uids", AsyncMock(return_value={103, 102})),
            patch.object(history_sync, "upsert_folder_stats", AsyncMock()),
            patch.object(history_sync, "_cache_message_detail", AsyncMock(return_value=(1, 0, 0))) as cache_detail,
        ):
            fetched, _att, _inline = await history_sync._sync_recent_uncached_messages(
                receiver, account, "INBOX", set()
            )

        self.assertEqual(fetched, 2)
        self.assertEqual(receiver.fetch_messages.await_count, 1)
        self.assertEqual([call.args[3].uid for call in cache_detail.await_args_list], [105, 104])

    async def test_unchecked_empty_body_is_marked_checked(self):
        history_sync = _load_history_sync_module()
        account = types.SimpleNamespace(id="account-1", user_uid="user-1", email="a@example.com")
        receiver = AsyncMock()
        receiver.fetch_message_detail.return_value = types.SimpleNamespace(
            uid=101,
            subject="empty",
            from_addr="from@example.com",
            to_addr="to@example.com",
            date="2026-07-03",
            is_read=True,
            is_starred=False,
            attachments=[],
            body_text="",
            body_html="",
        )

        rows = [
            [{"uid": 101, "date": "2026-07-03"}],
            [],
        ]

        with (
            patch.object(history_sync, "list_cached_messages_needing_body_check", AsyncMock(side_effect=rows)),
            patch.object(history_sync, "get_cached_message_detail", AsyncMock(return_value=None)),
            patch.object(history_sync, "_cache_message_assets", AsyncMock(return_value=("", "", 0, 0, []))),
            patch.object(history_sync, "upsert_cached_messages", AsyncMock()) as upsert,
            patch.object(history_sync, "upsert_cached_attachments", AsyncMock()),
            patch.object(history_sync, "mark_cached_messages_body_checked", AsyncMock()) as mark_checked,
            patch.object(history_sync, "mark_cached_messages_empty_body_checked", AsyncMock()) as mark_empty_checked,
        ):
            await history_sync._fill_unchecked_message_bodies(receiver, account, "INBOX", set())

        cached_message = upsert.await_args.args[0][0]
        self.assertTrue(cached_message.body_checked)
        self.assertEqual(cached_message.body_text, "")
        mark_checked.assert_awaited_with("account-1", "INBOX", [101])
        mark_empty_checked.assert_awaited_with("account-1", "INBOX", [101])

    async def test_body_fill_rechecks_checked_empty_messages(self):
        history_sync = _load_history_sync_module()
        account = types.SimpleNamespace(id="account-1", user_uid="user-1", email="a@example.com")
        receiver = AsyncMock()

        with (
            patch.object(
                history_sync,
                "list_cached_messages_needing_body_check",
                AsyncMock(side_effect=[[{"uid": 101, "date": "2026-07-03"}], []]),
            ) as list_needing,
            patch.object(history_sync, "_cache_message_detail", AsyncMock(return_value=(1, 0, 0))),
            patch.object(history_sync, "mark_cached_messages_body_checked", AsyncMock()),
            patch.object(history_sync, "mark_cached_messages_empty_body_checked", AsyncMock()),
        ):
            await history_sync._fill_unchecked_message_bodies(receiver, account, "INBOX", set())

        self.assertTrue(list_needing.await_args_list[0].kwargs["include_checked_empty"])

    async def test_history_sync_sets_pending_when_body_fill_has_more_work(self):
        history_sync = _load_history_sync_module()
        account = types.SimpleNamespace(id="account-1", user_uid="user-1", email="a@example.com", provider="qq")
        job = {"id": "job-1", "status": "running", "fetched_messages": 0}
        folder = types.SimpleNamespace(name="INBOX", path="INBOX")
        receiver = AsyncMock()
        receiver.fetch_folders.return_value = [folder]
        receiver.fetch_unseen_uids.return_value = []
        updates = []

        async def update_job(_job_id, **kwargs):
            updates.append(kwargs)

        with (
            patch.object(history_sync, "get_account_by_id", AsyncMock(return_value=account)),
            patch.object(history_sync, "get_history_sync_job_by_id", AsyncMock(return_value=job)),
            patch.object(history_sync, "sync_service", types.SimpleNamespace(suspend_account=AsyncMock())),
            patch.object(history_sync, "ensure_token", AsyncMock(return_value=object())),
            patch.object(history_sync, "ProviderFactory", types.SimpleNamespace(get_receiver=lambda _provider: receiver)),
            patch.object(history_sync, "update_history_sync_job", AsyncMock(side_effect=update_job)),
            patch.object(history_sync, "get_cached_count", AsyncMock(return_value=0)),
            patch.object(history_sync, "_is_paused", AsyncMock(return_value=False)),
            patch.object(history_sync, "_sync_recent_uncached_messages", AsyncMock(return_value=(0, 0, 0))),
            patch.object(history_sync, "_fill_unchecked_message_bodies", AsyncMock(return_value=(0, 0))),
            patch.object(history_sync, "_has_unchecked_message_bodies", AsyncMock(return_value=True)),
        ):
            await history_sync.run_history_sync("account-1", "job-1")

        self.assertTrue(any(update.get("status") == "pending" and update.get("current_page") == 2 for update in updates))


if __name__ == "__main__":
    unittest.main()
