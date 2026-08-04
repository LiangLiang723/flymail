import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


def _load_settings_route_module():
    fastapi_stub = types.ModuleType("fastapi")
    fastapi_stub.Request = object

    class _Router:
        def __init__(self, *args, **kwargs):
            pass

        def get(self, *args, **kwargs):
            return lambda func: func

        def post(self, *args, **kwargs):
            return lambda func: func

        def put(self, *args, **kwargs):
            return lambda func: func

    fastapi_stub.APIRouter = _Router

    deps_stub = types.ModuleType("deps")
    deps_stub.get_uid = AsyncMock(return_value="user-1")

    db_stub = types.ModuleType("db")
    db_stub.get_accounts = AsyncMock(return_value=[])
    db_stub.get_cached_attachment_rows = AsyncMock(return_value=[])
    db_stub.get_cached_count = AsyncMock(return_value=0)
    db_stub.get_cached_body_check_progress = AsyncMock(
        return_value={"total_count": 0, "checked_count": 0, "remaining_count": 0}
    )
    db_stub.get_folder_stats = AsyncMock(return_value={})
    db_stub.list_folder_stats_by_account = AsyncMock(return_value=[])
    db_stub.list_cached_counts_by_account = AsyncMock(return_value={})
    db_stub.folder_key_for_path = lambda value: str(value or "").strip().lower()
    db_stub.get_history_sync_job = AsyncMock(return_value=None)
    db_stub.list_account_folder_counts = AsyncMock(return_value=[])
    db_stub.list_history_sync_jobs = AsyncMock(return_value=[])
    db_stub.get_user_settings = AsyncMock(return_value={})
    db_stub.set_user_settings = AsyncMock()
    db_stub.update_account_credentials = AsyncMock(return_value=True)

    attachment_cache_stub = types.ModuleType("services.attachment_cache")
    attachment_cache_stub.DEFAULT_ATTACHMENT_CACHE_LIMIT_MB = 2048
    attachment_cache_stub.enforce_user_attachment_cache_limit = AsyncMock()
    attachment_cache_stub.get_shared_attachment_cache_usage = AsyncMock(return_value=0)
    attachment_cache_stub.get_user_attachment_cache_usage = AsyncMock(return_value=0)
    attachment_cache_stub.validate_attachment_cache_limit_mb = lambda value: int(value)

    history_sync_stub = types.ModuleType("services.history_sync")
    for name in (
        "is_full_history_sync_active",
        "pause_history_sync",
        "pause_folder_history_sync",
        "refresh_history_sync_job",
        "resume_history_sync",
        "resume_folder_history_sync",
        "retry_history_sync",
        "start_clear_cache",
        "start_folder_clear_cache",
        "start_folder_history_sync",
        "start_history_sync",
    ):
        setattr(history_sync_stub, name, AsyncMock())

    app_settings_stub = types.ModuleType("services.settings")
    app_settings_stub.async_load_settings = AsyncMock(return_value={})
    app_settings_stub.async_save_settings = AsyncMock(return_value={})

    sync_stub = types.ModuleType("services.sync")
    sync_stub.sync_service = types.SimpleNamespace()

    schemas_stub = types.ModuleType("schemas")
    for name in (
        "OAuthDiagnosticResponse",
        "SettingsResponse",
        "SettingsUpdateRequest",
        "SettingsUpdateResponse",
        "ProxyTestRequest",
        "ProxyTestResponse",
        "UnifiedSettingsRequest",
        "UnifiedSettingsResponse",
    ):
        setattr(schemas_stub, name, dict)

    previous = {
        name: sys.modules.get(name)
        for name in (
            "fastapi",
            "deps",
            "db",
            "services.attachment_cache",
            "services.history_sync",
            "services.settings",
            "services.sync",
            "schemas",
        )
    }
    sys.modules.update(
        {
            "fastapi": fastapi_stub,
            "deps": deps_stub,
            "db": db_stub,
            "services.attachment_cache": attachment_cache_stub,
            "services.history_sync": history_sync_stub,
            "services.settings": app_settings_stub,
            "services.sync": sync_stub,
            "schemas": schemas_stub,
        }
    )
    try:
        module_path = Path(__file__).resolve().parents[1] / "routes" / "settings.py"
        spec = importlib.util.spec_from_file_location("settings_route_for_test", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, value in previous.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


class HistorySyncProgressTest(unittest.IsolatedAsyncioTestCase):
    async def test_folder_progress_uses_summary_cache_count(self):
        settings = _load_settings_route_module()

        async def fake_get_folder_stats(account_id, folder_key):
            return {"total_count": 10, "unread_count": 2, "updated_at": 1}

        async def fake_get_cached_count(account_id, folder_key):
            return 7 if folder_key == "INBOX" else 0

        async def fake_get_history_sync_job(account_id, job_type="history_sync"):
            return None

        with (
            patch.object(
                settings,
                "list_account_folder_counts",
                AsyncMock(return_value=[{"folder_key": "inbox", "cached_count": 7}]),
            ),
            patch.object(settings, "get_folder_stats", fake_get_folder_stats),
            patch.object(settings, "get_cached_count", fake_get_cached_count),
            patch.object(settings, "get_history_sync_job", fake_get_history_sync_job),
        ):
            progress = await settings._build_folder_progress("account-1")

        inbox = next(item for item in progress if item["folder"] == "INBOX")
        self.assertEqual(inbox["cached_count"], 7)
        self.assertEqual(inbox["summary_count"], 7)
        self.assertFalse(inbox["is_synced"])

    async def test_folder_progress_total_is_not_less_than_cached_count(self):
        settings = _load_settings_route_module()

        async def fake_get_folder_stats(account_id, folder_key):
            return {"total_count": 0, "unread_count": 0, "updated_at": 1}

        async def fake_get_cached_count(account_id, folder_key):
            return 1 if folder_key == "Sent" else 0

        async def fake_get_history_sync_job(account_id, job_type="history_sync"):
            return None

        with (
            patch.object(
                settings,
                "list_account_folder_counts",
                AsyncMock(return_value=[{"folder_key": "sent", "cached_count": 1}]),
            ),
            patch.object(settings, "get_folder_stats", fake_get_folder_stats),
            patch.object(settings, "get_cached_count", fake_get_cached_count),
            patch.object(settings, "get_history_sync_job", fake_get_history_sync_job),
        ):
            progress = await settings._build_folder_progress("account-1")

        sent = next(item for item in progress if item["folder"] == "Sent")
        self.assertEqual(sent["cached_count"], 1)
        self.assertEqual(sent["total_count"], 1)
        self.assertTrue(sent["is_synced"])

    async def test_folder_progress_prefers_live_cache_when_summary_is_stale(self):
        settings = _load_settings_route_module()

        async def fake_get_folder_stats(account_id, folder_key):
            return {"total_count": 12, "unread_count": 2, "updated_at": 1}

        async def fake_get_cached_count(account_id, folder_key):
            return 12 if folder_key == "INBOX" else 0

        async def fake_get_history_sync_job(account_id, job_type="history_sync"):
            return None

        with (
            patch.object(
                settings,
                "list_account_folder_counts",
                AsyncMock(return_value=[{"folder_key": "inbox", "cached_count": 10}]),
            ),
            patch.object(settings, "get_folder_stats", fake_get_folder_stats),
            patch.object(settings, "get_cached_count", fake_get_cached_count),
            patch.object(settings, "get_history_sync_job", fake_get_history_sync_job),
        ):
            progress = await settings._build_folder_progress("account-1")

        inbox = next(item for item in progress if item["folder"] == "INBOX")
        self.assertEqual(inbox["cached_count"], 12)
        self.assertEqual(inbox["summary_count"], 12)
        self.assertTrue(inbox["is_synced"])

    async def test_folder_progress_includes_discovered_custom_folders(self):
        settings = _load_settings_route_module()
        rows = [
            {
                "folder_key": "inbox",
                "folder_path": "INBOX",
                "display_name": "收件箱",
                "cached_count": 50,
                "total_count": 542,
                "unread_count": 254,
            },
            {
                "folder_key": "oa",
                "folder_path": "OA",
                "display_name": "OA",
                "cached_count": 267,
                "total_count": 575,
                "unread_count": 267,
            },
            {
                "folder_key": "rovo",
                "folder_path": "ROVO",
                "display_name": "ROVO",
                "cached_count": 556,
                "total_count": 689,
                "unread_count": 556,
            },
        ]

        async def fake_get_folder_stats(account_id, folder_path):
            row = next((item for item in rows if item["folder_path"] == folder_path), None)
            return {
                "total_count": row["total_count"] if row else 0,
                "unread_count": row["unread_count"] if row else 0,
                "updated_at": 1,
            }

        async def fake_get_cached_count(account_id, folder_path):
            row = next((item for item in rows if item["folder_path"] == folder_path), None)
            return row["cached_count"] if row else 0

        with (
            patch.object(settings, "list_account_folder_counts", AsyncMock(return_value=rows)),
            patch.object(settings, "get_folder_stats", fake_get_folder_stats),
            patch.object(settings, "get_cached_count", fake_get_cached_count),
            patch.object(settings, "get_history_sync_job", AsyncMock(return_value=None)),
        ):
            progress = await settings._build_folder_progress("account-1")

        self.assertEqual(
            [item["folder"] for item in progress],
            ["INBOX", "Sent", "Drafts", "Junk", "Trash", "OA", "ROVO"],
        )
        oa = next(item for item in progress if item["folder"] == "OA")
        self.assertEqual(oa["label"], "OA")
        self.assertEqual(oa["cached_count"], 267)
        self.assertEqual(oa["total_count"], 575)

    async def test_folder_progress_includes_live_body_check_counts(self):
        settings = _load_settings_route_module()

        async def fake_get_folder_stats(account_id, folder_key):
            return {"total_count": 12, "unread_count": 2, "updated_at": 1}

        async def fake_get_cached_count(account_id, folder_key):
            return 12 if folder_key == "INBOX" else 0

        body_progress = AsyncMock(
            return_value={"total_count": 12, "checked_count": 4, "remaining_count": 8}
        )

        with (
            patch.object(settings, "list_account_folder_counts", AsyncMock(return_value=[])),
            patch.object(settings, "get_folder_stats", fake_get_folder_stats),
            patch.object(settings, "get_cached_count", fake_get_cached_count),
            patch.object(settings, "get_cached_body_check_progress", body_progress),
            patch.object(settings, "get_history_sync_job", AsyncMock(return_value=None)),
        ):
            progress = await settings._build_folder_progress(
                "account-1",
                body_progress_folder="INBOX",
            )

        body_progress.assert_awaited_once_with("account-1", "INBOX")
        inbox = next(item for item in progress if item["folder"] == "INBOX")
        self.assertEqual(inbox["body_total_count"], 12)
        self.assertEqual(inbox["body_checked_count"], 4)
        self.assertEqual(inbox["body_remaining_count"], 8)

    async def test_history_job_list_uses_bulk_folder_progress_queries(self):
        settings = _load_settings_route_module()
        account = types.SimpleNamespace(
            id="account-1",
            email="a@example.com",
            remark="",
            provider="custom",
            status="connected",
        )
        history_job = {
            "id": "job-1",
            "account_id": "account-1",
            "job_type": "history_sync",
            "status": "running",
            "current_folder": "INBOX",
            "current_page": 1,
        }
        with (
            patch.object(settings, "get_accounts", AsyncMock(return_value=[account])),
            patch.object(settings, "list_history_sync_jobs", AsyncMock(return_value=[history_job])),
            patch.object(
                settings,
                "list_account_folder_counts",
                AsyncMock(return_value=[{
                    "folder_key": "inbox",
                    "folder_path": "INBOX",
                    "display_name": "收件箱",
                    "total_count": 10,
                    "unread_count": 2,
                    "cached_count": 7,
                    "updated_at": 1,
                }]),
            ) as list_counts,
            patch.object(
                settings,
                "list_folder_stats_by_account",
                AsyncMock(return_value=[{
                    "folder": "INBOX",
                    "total_count": 10,
                    "unread_count": 2,
                    "updated_at": 1,
                }]),
            ) as list_stats,
            patch.object(
                settings,
                "list_cached_counts_by_account",
                AsyncMock(return_value={"inbox": 7}),
            ) as list_cached,
            patch.object(settings, "get_folder_stats", AsyncMock(side_effect=AssertionError("N+1 stats query"))),
            patch.object(settings, "get_cached_count", AsyncMock(side_effect=AssertionError("N+1 cache query"))),
            patch.object(settings, "get_history_sync_job", AsyncMock(side_effect=AssertionError("N+1 job query"))),
        ):
            result = await settings.get_history_sync_jobs(object())

        self.assertEqual(result["jobs"][0]["folder_progress"][0]["cached_count"], 7)
        list_counts.assert_awaited_once_with("account-1")
        list_stats.assert_awaited_once_with("account-1")
        list_cached.assert_awaited_once_with("account-1")

    async def test_history_sync_detail_uses_latest_job_of_each_type(self):
        settings = _load_settings_route_module()
        account = types.SimpleNamespace(
            id="account-1",
            email="a@example.com",
            remark="",
            provider="custom",
        )
        newest_job = {
            "id": "job-new",
            "account_id": "account-1",
            "job_type": "history_sync",
            "status": "completed",
            "updated_at": 20,
        }
        older_job = {
            "id": "job-old",
            "account_id": "account-1",
            "job_type": "history_sync",
            "status": "failed",
            "updated_at": 10,
            "error_message": "旧失败记录",
        }

        with (
            patch.object(settings, "get_accounts", AsyncMock(return_value=[account])),
            patch.object(
                settings,
                "list_history_sync_jobs",
                AsyncMock(return_value=[newest_job, older_job]),
            ),
            patch.object(settings, "_build_account_folder_progress", AsyncMock(return_value=[])),
        ):
            result = await settings.get_history_sync_job_detail("account-1", object())

        self.assertEqual(result["job"]["id"], "job-new")
        self.assertEqual(result["job"]["status"], "completed")

    async def test_start_history_sync_rejects_disabled_account(self):
        settings = _load_settings_route_module()
        account = types.SimpleNamespace(id="account-1", status="offline")

        with (
            patch.object(settings, "get_accounts", AsyncMock(return_value=[account])),
            patch.object(settings, "start_history_sync", AsyncMock(return_value=True)) as start_sync,
        ):
            result = await settings.start_history_sync_job("account-1", object())

        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "account_disabled")
        start_sync.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
