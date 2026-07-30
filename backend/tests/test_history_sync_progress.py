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
    db_stub.get_folder_stats = AsyncMock(return_value={})
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
