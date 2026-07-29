import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from providers.base import Folder, Message, MessageList


def _load_messages_route_module():
    fastapi_stub = types.ModuleType("fastapi")

    class Router:
        def __init__(self, *args, **kwargs):
            pass

        def get(self, *args, **kwargs):
            return lambda fn: fn

        def post(self, *args, **kwargs):
            return lambda fn: fn

        def delete(self, *args, **kwargs):
            return lambda fn: fn

    fastapi_stub.APIRouter = Router
    fastapi_stub.Body = lambda *args, **kwargs: None
    fastapi_stub.File = lambda *args, **kwargs: None
    fastapi_stub.Query = lambda default=None, *args, **kwargs: default
    fastapi_stub.Request = object
    fastapi_stub.UploadFile = object

    responses_stub = types.ModuleType("fastapi.responses")
    responses_stub.FileResponse = object

    db_stub = types.ModuleType("db")
    for name in (
        "adjust_account_folder_unread",
        "batch_delete_cached_messages",
        "batch_update_is_read",
        "batch_update_cached_messages_read",
        "delete_pending_read_sync",
        "enqueue_pending_read_sync",
        "get_accounts",
        "get_cached_attachment",
        "get_cached_is_read",
        "get_cached_message_detail",
        "get_cached_messages_by_folder",
        "get_folder_filter_counts",
        "get_folder_stats",
        "get_unified_inbox_messages",
        "get_unified_inbox_stats",
        "get_unified_inbox_filter_counts",
        "get_user_settings",
        "mark_all_cached_messages_read",
        "list_account_folder_counts",
        "list_cached_attachments",
        "search_cached_messages_by_folder",
        "update_cached_message_read",
        "update_cached_message_storage_path",
        "upsert_cached_attachments",
        "upsert_cached_messages",
        "upsert_folder_stats",
        "delete_cached_message",
    ):
        setattr(db_stub, name, AsyncMock())

    data_paths_stub = types.ModuleType("data_paths")
    for name in (
        "UPLOADS_DIR",
        "coalesce_message_date",
        "ensure_data_dirs",
        "ensure_message_file_location",
    ):
        setattr(data_paths_stub, name, object())
    data_paths_stub.ensure_data_dirs = lambda: None

    deps_stub = types.ModuleType("deps")
    deps_stub.get_uid = object()

    errors_stub = types.ModuleType("errors")
    errors_stub.AppError = Exception

    models_stub = types.ModuleType("models")
    models_stub.Account = object
    models_stub.CachedAttachment = object

    factory_stub = types.ModuleType("providers.factory")
    factory_stub.ProviderFactory = object()

    helpers_stub = types.ModuleType("routes._helpers")
    helpers_stub._OUTLOOK_RECONNECTING_MSG = ""
    helpers_stub._find_account_or_error = object()
    helpers_stub._is_outlook_connection_error = lambda *args, **kwargs: False
    helpers_stub._notify_if_permanent_token_error = object()
    helpers_stub._safe_disconnect = object()
    helpers_stub._with_outlook_retry = object()

    schemas_stub = types.ModuleType("schemas")
    for name in (
        "BatchDeleteRequest",
        "BatchDeleteResponse",
        "BatchMarkReadRequest",
        "BatchMarkReadResponse",
        "MarkAllReadRequest",
        "MarkAllReadResponse",
        "DeleteResponse",
        "MessageItem",
        "MessageListResponse",
        "MessageResponse",
        "MarkReadRequest",
        "PrefetchMessagesRequest",
        "PrefetchMessagesResponse",
        "StatusResponse",
        "UploadAttachmentResponse",
        "RegisterNasAttachmentRequest",
        "SaveAttachmentToNasRequest",
        "SaveAttachmentToNasResponse",
    ):
        setattr(schemas_stub, name, object)

    attachments_stub = types.ModuleType("services.attachments")
    attachments_stub.MAX_SINGLE_FILE_SIZE = 100 * 1024 * 1024
    for name in (
        "build_upload_path",
        "is_temp_upload_path",
        "resolve_compose_attachment_path",
        "resolve_user_attachment_path",
        "sanitize_attachment_filename",
        "unique_target_file",
    ):
        setattr(attachments_stub, name, object())

    sync_stub = types.ModuleType("services.sync")
    sync_stub.sync_service = object()

    mail_cache_stub = types.ModuleType("services.mail_cache")
    mail_cache_stub.sync_missing_messages = AsyncMock(return_value=1)
    mail_cache_stub.sync_missing_message_summaries = AsyncMock(return_value=1)

    token_stub = types.ModuleType("services.token")
    token_stub.ensure_token = object()

    logger_stub = types.ModuleType("utils.logger")
    logger_stub.get_logger = lambda name: types.SimpleNamespace(
        debug=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
    )

    tasks_stub = types.ModuleType("utils.tasks")
    tasks_stub.create_background_task = lambda *args, **kwargs: None

    module_names = (
        "db",
        "fastapi",
        "fastapi.responses",
        "data_paths",
        "deps",
        "errors",
        "models",
        "providers.factory",
        "routes._helpers",
        "schemas",
        "services.attachments",
        "services.sync",
        "services.mail_cache",
        "services.token",
        "utils.logger",
        "utils.tasks",
    )
    previous = {name: sys.modules.get(name) for name in module_names}
    sys.modules.update(
        {
            "db": db_stub,
            "fastapi": fastapi_stub,
            "fastapi.responses": responses_stub,
            "data_paths": data_paths_stub,
            "deps": deps_stub,
            "errors": errors_stub,
            "models": models_stub,
            "providers.factory": factory_stub,
            "routes._helpers": helpers_stub,
            "schemas": schemas_stub,
            "services.attachments": attachments_stub,
            "services.sync": sync_stub,
            "services.mail_cache": mail_cache_stub,
            "services.token": token_stub,
            "utils.logger": logger_stub,
            "utils.tasks": tasks_stub,
        }
    )
    try:
        module_path = Path(__file__).resolve().parents[1] / "routes" / "messages.py"
        spec = importlib.util.spec_from_file_location("messages_route_for_test", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, value in previous.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


class MessageFolderResolutionTest(unittest.IsolatedAsyncioTestCase):
    async def test_cached_list_uses_stable_database_id(self):
        messages = _load_messages_route_module()
        payload = {
            "messages": [
                {
                    "id": "account-1_Sent_aaa_12",
                    "uid": 12,
                    "subject": "same",
                    "folder": "Sent",
                    "account_id": "account-1",
                },
                {
                    "id": "account-1_Sent_Messages_bbb_12",
                    "uid": 12,
                    "subject": "same",
                    "folder": "Sent Messages",
                    "account_id": "account-1",
                },
            ],
            "total": 2,
            "unread_total": 0,
            "page": 1,
            "page_size": 50,
        }

        response = messages._build_list_response(payload, "account-1", {})

        self.assertEqual(
            [item["id"] for item in response["messages"]],
            ["account-1_Sent_aaa_12", "account-1_Sent_Messages_bbb_12"],
        )

    async def test_manual_refresh_schedules_missing_uid_backfill_without_waiting(self):
        messages = _load_messages_route_module()
        account = types.SimpleNamespace(
            id="account-1",
            user_uid="user-1",
            email="user@example.com",
            provider="qq",
            status="connected",
        )
        result = types.SimpleNamespace(
            messages=[],
            total=599,
            unread_total=0,
        )

        receiver = AsyncMock()
        receiver.fetch_messages.return_value = result
        receiver.fetch_unseen_uids.return_value = []
        receiver.disconnect = AsyncMock()

        messages._get_account = AsyncMock(return_value=("user-1", account))
        messages.ensure_account_token = AsyncMock(return_value=object())
        messages.ProviderFactory = types.SimpleNamespace(get_receiver=lambda _provider: receiver)
        messages._resolve_remote_folder = AsyncMock(return_value="Sent Messages")
        messages._safe_disconnect = AsyncMock()
        async def run_operation(_account, operation):
            return await operation()

        messages._with_outlook_retry = AsyncMock(side_effect=run_operation)
        messages._cache_remote_page = AsyncMock()
        messages._load_local_messages = AsyncMock(return_value={"messages": [], "total": 598})
        messages.sync_service = types.SimpleNamespace(
            is_account_suspended=lambda _account_id: False,
            refresh_clients=AsyncMock(),
        )
        messages._schedule_missing_summary_sync = Mock(return_value=True)

        await messages.refresh_messages(
            request=object(),
            folder="Sent",
            page_size=50,
            account_id="account-1",
        )

        messages._schedule_missing_summary_sync.assert_called_once_with(
            account,
            "Sent Messages",
            "user-1",
        )
        messages.sync_service.refresh_clients.assert_awaited_once_with("account-1", "Sent", user_uid="user-1")

    async def test_missing_summary_sync_is_deduplicated_per_folder(self):
        messages = _load_messages_route_module()
        account = types.SimpleNamespace(id="account-1", email="user@example.com")
        background_tasks = []

        def capture_background_task(coro, name=""):
            background_tasks.append((coro, name))
            coro.close()
            return types.SimpleNamespace(add_done_callback=lambda _callback: None)

        messages.create_background_task = capture_background_task
        messages._MISSING_SUMMARY_REFRESHING.clear()

        first = messages._schedule_missing_summary_sync(account, "OA", "user-1")
        second = messages._schedule_missing_summary_sync(account, "OA", "user-1")

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(len(background_tasks), 1)
        self.assertEqual(background_tasks[0][1], "refresh_missing_summaries")
        messages._MISSING_SUMMARY_REFRESHING.clear()

    async def test_missing_summary_sync_notifies_each_batch_and_clears_dedup(self):
        messages = _load_messages_route_module()
        account = types.SimpleNamespace(id="account-1", email="user@example.com")
        mail_cache_stub = types.ModuleType("services.mail_cache")

        async def sync_summaries(_account, _folder, *, on_batch):
            await on_batch(100, 125)
            await on_batch(125, 125)
            return 125

        mail_cache_stub.sync_missing_message_summaries = AsyncMock(side_effect=sync_summaries)
        messages.sync_service = types.SimpleNamespace(refresh_clients=AsyncMock())
        messages._MISSING_SUMMARY_REFRESHING.add(("account-1", "OA"))

        with patch.dict(sys.modules, {"services.mail_cache": mail_cache_stub}):
            await messages._run_missing_summary_sync(account, "OA", "user-1")

        mail_cache_stub.sync_missing_message_summaries.assert_awaited_once()
        self.assertEqual(messages.sync_service.refresh_clients.await_count, 2)
        self.assertNotIn(("account-1", "OA"), messages._MISSING_SUMMARY_REFRESHING)

    async def test_incomplete_later_page_fetches_remote_before_returning_partial_cache(self):
        messages = _load_messages_route_module()
        account = types.SimpleNamespace(
            id="account-1",
            user_uid="user-1",
            email="user@example.com",
            provider="custom",
            status="connected",
        )
        remote_message = Message(
            id="remote-60",
            uid=60,
            subject="remote page",
            from_addr="from@example.com",
            to_addr="to@example.com",
            date="2026-07-01T00:00:00Z",
            folder="OA",
        )
        remote_result = MessageList(
            messages=[remote_message],
            total=100,
            unread_total=0,
            page=2,
            page_size=50,
        )

        messages._get_account = AsyncMock(return_value=("user-1", account))
        messages._load_local_messages = AsyncMock(return_value={
            "messages": [{"id": "stale-local", "uid": 5, "folder": "OA"}],
            "total": 51,
            "unread_total": 0,
            "page": 2,
            "page_size": 50,
            "filter_counts": {"all": 51, "unread": 0, "read": 51, "attachments": 0},
        })
        messages._get_effective_folder_stats = AsyncMock(return_value={
            "updated_at": 1,
            "total_count": 100,
            "unread_count": 0,
        })
        messages._fetch_remote_page_to_cache = AsyncMock(return_value=(remote_result, ""))
        messages.get_folder_filter_counts = AsyncMock(return_value={
            "all": 51,
            "unread": 0,
            "read": 51,
            "attachments": 0,
        })
        messages.sync_service = types.SimpleNamespace(
            is_account_suspended=lambda _account_id: False,
        )

        response = await messages.list_messages(
            request=object(),
            folder="OA",
            page=2,
            page_size=50,
            account_id="account-1",
            read_filter="",
            attachment_filter=False,
        )

        self.assertEqual(response["messages"][0]["uid"], 60)
        messages._fetch_remote_page_to_cache.assert_awaited_once_with(
            user_uid="user-1",
            account=account,
            folder="OA",
            page=2,
            page_size=50,
        )

    async def test_resolves_netease_sent_folder_by_display_name(self):
        messages = _load_messages_route_module()

        class Receiver:
            async def fetch_folders(self):
                return [
                    Folder(name="收件箱", path="INBOX"),
                    Folder(name="已发送", path="&XfJT0ZAB-"),
                ]

        resolved = await messages._resolve_remote_folder(Receiver(), "Sent")

        self.assertEqual(resolved, "&XfJT0ZAB-")

    async def test_resolves_gmail_sent_folder_by_path_alias(self):
        messages = _load_messages_route_module()

        class Receiver:
            async def fetch_folders(self):
                return [
                    Folder(name="收件箱", path="INBOX"),
                    Folder(name="已发送", path="[Gmail]/Sent Mail"),
                ]

        resolved = await messages._resolve_remote_folder(Receiver(), "Sent")

        self.assertEqual(resolved, "[Gmail]/Sent Mail")

    async def test_resolves_gmail_localized_sent_folder_by_modified_utf7_path(self):
        messages = _load_messages_route_module()

        class Receiver:
            async def fetch_folders(self):
                return [
                    Folder(name="收件箱", path="INBOX"),
                    Folder(name="已发送", path="[Gmail]/&XfJT0ZCuTvY-"),
                ]

        resolved = await messages._resolve_remote_folder(Receiver(), "Sent")

        self.assertEqual(resolved, "[Gmail]/&XfJT0ZCuTvY-")

    async def test_sent_zero_stats_are_rechecked_after_ttl(self):
        messages = _load_messages_route_module()

        folder_stats = {
            "total_count": 0,
            "unread_count": 0,
            "updated_at": 1000,
        }
        local_data = {"messages": [], "total": 0}

        messages.time.time = lambda: 1000 + messages.ZERO_COUNT_RECHECK_SECONDS + 1

        self.assertFalse(messages._trust_zero_folder_stats("Sent", folder_stats))
        self.assertFalse(
            messages._local_page_is_complete(
                local_data,
                folder_stats,
                page=1,
                page_size=50,
                trust_zero_stats=messages._trust_zero_folder_stats("Sent", folder_stats),
            )
        )

    async def test_recent_sent_zero_stats_are_not_trusted(self):
        messages = _load_messages_route_module()

        folder_stats = {
            "total_count": 0,
            "unread_count": 0,
            "updated_at": 1000,
        }
        local_data = {"messages": [], "total": 0}

        self.assertFalse(messages._trust_zero_folder_stats("Sent", folder_stats))
        self.assertFalse(
            messages._local_page_is_complete(
                local_data,
                folder_stats,
                page=1,
                page_size=50,
                trust_zero_stats=messages._trust_zero_folder_stats("Sent", folder_stats),
            )
        )

    async def test_remote_page_fetch_returns_error_on_timeout(self):
        messages = _load_messages_route_module()

        async def slow_operation(_account, _operation):
            await messages.asyncio.sleep(1)

        account = types.SimpleNamespace(
            id="account-1",
            email="user@example.com",
            provider="gmail",
            status="online",
        )
        messages.REMOTE_PAGE_FETCH_TIMEOUT_SECONDS = 0.01
        messages._with_outlook_retry = slow_operation
        messages.sync_service = types.SimpleNamespace(is_account_suspended=lambda _account_id: False)

        result, error = await messages._fetch_remote_page_to_cache(
            user_uid="user-1",
            account=account,
            folder="Sent",
            page=1,
            page_size=50,
        )

        self.assertIsNone(result)
        self.assertIn("超时", error)


    async def test_cached_detail_with_body_is_complete_without_cached_attachments(self):
        messages = _load_messages_route_module()
        cached = {"body_html": "<p>ok</p>", "body_text": "", "has_attachments": True}

        complete = await messages._cached_detail_assets_complete("account-1", 1, "INBOX", cached)

        self.assertTrue(complete)

    async def test_mark_read_queues_remote_failure_and_updates_local(self):
        messages = _load_messages_route_module()
        account = types.SimpleNamespace(
            id="account-1",
            user_uid="user-1",
            email="user@example.com",
            provider="gmail",
            status="connected",
        )
        body = types.SimpleNamespace(
            account_id="account-1",
            message_id="account-1_INBOX_hash_42",
            folder="INBOX",
        )

        messages._get_account = AsyncMock(return_value=("user-1", account))
        messages.get_cached_is_read = AsyncMock(return_value=False)
        messages._mark_remote_message_read = AsyncMock(side_effect=TimeoutError("timed out"))
        messages._notify_if_permanent_token_error = AsyncMock()
        messages._is_outlook_connection_error = lambda *_args, **_kwargs: False
        messages.enqueue_pending_read_sync = AsyncMock()
        messages.update_cached_message_read = AsyncMock(return_value=True)
        messages.adjust_account_folder_unread = AsyncMock()
        messages._adjust_folder_unread_stats = AsyncMock()
        messages.sync_service = types.SimpleNamespace(notify_message_state_changed=AsyncMock())

        result = await messages.mark_message_as_read(request=object(), body=body)

        self.assertTrue(result["success"])
        messages.enqueue_pending_read_sync.assert_awaited_once_with(
            "account-1", "user-1", 42, "INBOX", True, "timed out"
        )
        messages.update_cached_message_read.assert_awaited_once_with("account-1", 42, "INBOX", True)

if __name__ == "__main__":
    unittest.main()
