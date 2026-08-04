import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock


def _load_accounts_route_module():
    fastapi_stub = types.ModuleType("fastapi")

    class _Router:
        def __init__(self, *args, **kwargs):
            pass

        def get(self, *args, **kwargs):
            return lambda func: func

        def post(self, *args, **kwargs):
            return lambda func: func

        def put(self, *args, **kwargs):
            return lambda func: func

        def delete(self, *args, **kwargs):
            return lambda func: func

    fastapi_stub.APIRouter = _Router
    fastapi_stub.Body = lambda *args, **kwargs: None
    fastapi_stub.File = lambda *args, **kwargs: None
    fastapi_stub.Path = lambda *args, **kwargs: None
    fastapi_stub.Request = object
    fastapi_stub.UploadFile = object

    fastapi_responses_stub = types.ModuleType("fastapi.responses")
    fastapi_responses_stub.FileResponse = object

    errors_stub = types.ModuleType("errors")

    class _AppError(Exception):
        def __init__(self, code, message):
            self.code = code
            self.message = message
            super().__init__(message)

    errors_stub.AppError = _AppError

    db_stub = types.ModuleType("db")
    for name in (
        "activate_account",
        "get_accounts",
        "create_account",
        "deactivate_account",
        "list_history_sync_jobs",
        "reorder_accounts",
        "update_account_info",
        "update_account_icon",
    ):
        setattr(db_stub, name, AsyncMock())

    deps_stub = types.ModuleType("deps")
    deps_stub.get_uid = AsyncMock(return_value="user-1")

    models_stub = types.ModuleType("models")
    models_stub.Account = object

    providers_base_stub = types.ModuleType("providers.base")
    providers_base_stub.Credentials = object

    factory_stub = types.ModuleType("providers.factory")
    factory_stub.ProviderFactory = object()

    auth_stub = types.ModuleType("routes.auth")
    for name in (
        "_build_oauth_result_html",
        "_extract_oauth_frontend_url_from_state",
        "_extract_oauth_provider_from_state",
        "_extract_oauth_state_data",
        "_extract_oauth_uid_from_state",
    ):
        setattr(auth_stub, name, object())

    account_icons_stub = types.ModuleType("services.account_icons")
    account_icons_stub.ACCOUNT_ICON_PRESET_IDS = frozenset({"work"})
    account_icons_stub.MAX_ACCOUNT_ICON_BYTES = 10 * 1024 * 1024
    account_icons_stub.commit_staged_account_icon = Mock()
    account_icons_stub.delete_account_icon = Mock()
    account_icons_stub.resolve_account_icon = Mock(return_value=None)
    account_icons_stub.rollback_staged_account_icon = Mock()
    account_icons_stub.stage_account_icon = Mock()

    account_presenter_stub = types.ModuleType("services.account_presenter")
    account_presenter_stub.account_icon_fields = lambda _account: {
        "icon_type": "default",
        "icon_value": "",
        "icon_url": "",
    }

    history_sync_stub = types.ModuleType("services.history_sync")
    for name in ("schedule_history_sync", "start_clear_cache", "start_delete_account"):
        setattr(history_sync_stub, name, AsyncMock())

    mail_cache_stub = types.ModuleType("services.mail_cache")
    mail_cache_stub.initial_sync = AsyncMock()

    app_settings_stub = types.ModuleType("services.settings")
    app_settings_stub.async_load_settings = AsyncMock(return_value={})
    app_settings_stub.async_save_settings = AsyncMock(return_value={})

    sync_stub = types.ModuleType("services.sync")
    sync_stub.sync_service = types.SimpleNamespace(
        add_account=AsyncMock(),
        reauth_account_ids=set(),
    )

    token_stub = types.ModuleType("services.token")
    token_stub.ensure_token = AsyncMock(return_value=object())

    logger_stub = types.ModuleType("utils.logger")
    logger_stub.get_logger = lambda _name: types.SimpleNamespace(
        debug=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
    )

    tasks_stub = types.ModuleType("utils.tasks")
    tasks_stub.create_background_task = Mock()
    tasks_stub.create_background_task.side_effect = lambda coro, **_kwargs: coro.close()

    schemas_stub = types.ModuleType("schemas")
    for name in (
        "AccountAddResponse",
        "AccountListResponse",
        "AccountTestResponse",
        "AccountOrderRequest",
        "AccountUpdateRequest",
        "AccountIconPresetRequest",
        "AccountIconResponse",
        "AuthCodeAccountRequest",
        "CustomAccountRequest",
        "AuthUrlRequest",
        "AuthUrlResponse",
        "DeleteResponse",
        "MessageResponse",
    ):
        setattr(schemas_stub, name, object)

    modules = {
        "fastapi": fastapi_stub,
        "fastapi.responses": fastapi_responses_stub,
        "errors": errors_stub,
        "db": db_stub,
        "deps": deps_stub,
        "models": models_stub,
        "providers.base": providers_base_stub,
        "providers.factory": factory_stub,
        "routes.auth": auth_stub,
        "services.account_icons": account_icons_stub,
        "services.account_presenter": account_presenter_stub,
        "services.history_sync": history_sync_stub,
        "services.mail_cache": mail_cache_stub,
        "services.settings": app_settings_stub,
        "services.sync": sync_stub,
        "services.token": token_stub,
        "utils.logger": logger_stub,
        "utils.tasks": tasks_stub,
        "schemas": schemas_stub,
    }
    previous = {name: sys.modules.get(name) for name in modules}
    sys.modules.update(modules)
    try:
        module_path = Path(__file__).resolve().parents[1] / "routes" / "accounts.py"
        spec = importlib.util.spec_from_file_location("accounts_route_for_test", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module, db_stub, sync_stub, tasks_stub
    finally:
        for name, value in previous.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


class AccountUpdateTest(unittest.IsolatedAsyncioTestCase):
    async def test_update_account_reloads_listener_for_new_poll_interval(self):
        accounts, db_stub, sync_stub, tasks_stub = _load_accounts_route_module()
        db_stub.update_account_info.return_value = True
        body = types.SimpleNamespace(
            remark="",
            group_name="",
            hide_email=False,
            poll_interval_seconds=60,
        )

        result = await accounts.update_account("account-1", object(), body)

        self.assertTrue(result["success"])
        db_stub.update_account_info.assert_awaited_once_with(
            "account-1",
            "user-1",
            "",
            "",
            False,
            60,
        )
        sync_stub.sync_service.add_account.assert_called_once_with("account-1")
        tasks_stub.create_background_task.assert_called_once()
        self.assertEqual(tasks_stub.create_background_task.call_args.kwargs["name"], "reload_account_imap")

    async def test_reorder_accounts_saves_current_users_complete_order(self):
        accounts, db_stub, _sync_stub, _tasks_stub = _load_accounts_route_module()
        db_stub.reorder_accounts.return_value = True
        body = types.SimpleNamespace(account_ids=["account-2", "account-1"])

        result = await accounts.reorder_account_list(object(), body)

        self.assertEqual(result, {"success": True})
        db_stub.reorder_accounts.assert_awaited_once_with(
            "user-1",
            ["account-2", "account-1"],
        )

    async def test_reorder_accounts_rejects_invalid_order(self):
        accounts, db_stub, _sync_stub, _tasks_stub = _load_accounts_route_module()
        db_stub.reorder_accounts.return_value = False
        body = types.SimpleNamespace(account_ids=["account-1", "outside"])

        with self.assertRaises(accounts.AppError) as raised:
            await accounts.reorder_account_list(object(), body)

        self.assertEqual(raised.exception.code, 400)
        self.assertEqual(raised.exception.message, "邮箱账号顺序无效，请刷新后重试")


if __name__ == "__main__":
    unittest.main()
