import importlib.util
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock


class _Model:
    model_fields = {}

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _load_db_module():
    aiomysql_stub = types.ModuleType("aiomysql")
    aiomysql_stub.Pool = object
    aiomysql_stub.Connection = object
    aiomysql_stub.create_pool = AsyncMock()

    pymysql_stub = types.ModuleType("pymysql")
    pymysql_stub.err = types.SimpleNamespace(
        InterfaceError=type("InterfaceError", (Exception,), {}),
        OperationalError=type("OperationalError", (Exception,), {}),
    )

    data_paths_stub = types.ModuleType("data_paths")
    data_paths_stub.ensure_data_dirs = lambda: None

    models_stub = types.ModuleType("models")
    for name in ("Account", "CachedAttachment", "CachedMessage", "Notification", "Signature", "User"):
        setattr(models_stub, name, _Model)

    logger_stub = types.ModuleType("utils.logger")
    logger_stub.get_logger = lambda _name: types.SimpleNamespace(
        debug=lambda *args, **kwargs: None,
        info=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
    )

    modules = {
        "aiomysql": aiomysql_stub,
        "pymysql": pymysql_stub,
        "data_paths": data_paths_stub,
        "models": models_stub,
        "utils.logger": logger_stub,
    }
    previous = {name: sys.modules.get(name) for name in modules}
    sys.modules.update(modules)
    try:
        module_path = Path(__file__).resolve().parents[1] / "db" / "__init__.py"
        spec = importlib.util.spec_from_file_location("db_for_signature_defaults_test", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, value in previous.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


class _RecordingDB:
    def __init__(self):
        self.calls = []

    async def execute(self, sql, params=None):
        self.calls.append((" ".join(sql.split()), tuple(params or ())))
        return SimpleNamespace(rowcount=1)


class SignatureDefaultScopeTest(unittest.IsolatedAsyncioTestCase):
    async def test_new_and_reply_defaults_are_cleared_only_in_same_user_account_scope(self):
        db = _load_db_module()
        clear_defaults = getattr(db, "_clear_signature_defaults", None)
        self.assertIsNotNone(clear_defaults, "signature default scope helper must be implemented")
        recorder = _RecordingDB()
        signature = SimpleNamespace(
            id=17,
            user_uid="user-a",
            account_id="account-a",
            is_default=1,
            is_reply_default=1,
        )

        await clear_defaults(recorder, signature)

        self.assertEqual(len(recorder.calls), 2)
        first_sql, first_params = recorder.calls[0]
        second_sql, second_params = recorder.calls[1]
        self.assertIn("SET is_default = 0", first_sql)
        self.assertIn("user_uid = ?", first_sql)
        self.assertIn("account_id = ?", first_sql)
        self.assertIn("id <> ?", first_sql)
        self.assertEqual(first_params, ("user-a", "account-a", 17))
        self.assertIn("SET is_reply_default = 0", second_sql)
        self.assertEqual(second_params, ("user-a", "account-a", 17))

    async def test_no_default_flags_do_not_issue_updates(self):
        db = _load_db_module()
        recorder = _RecordingDB()
        signature = SimpleNamespace(
            id=0,
            user_uid="user-a",
            account_id="",
            is_default=0,
            is_reply_default=0,
        )

        await db._clear_signature_defaults(recorder, signature)

        self.assertEqual(recorder.calls, [])

    def test_signature_schema_exposes_reply_default(self):
        backend_root = Path(__file__).resolve().parents[1]
        model_source = (backend_root / "models" / "__init__.py").read_text(encoding="utf-8")
        schema_source = (backend_root / "schemas.py").read_text(encoding="utf-8")
        route_source = (backend_root / "routes" / "signatures.py").read_text(encoding="utf-8")

        self.assertIn("is_reply_default", model_source)
        self.assertIn("is_reply_default", schema_source)
        self.assertIn("is_reply_default", route_source)
        self.assertIn("get_account_by_id", route_source)


if __name__ == "__main__":
    unittest.main()
