import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


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
        spec = importlib.util.spec_from_file_location("db_for_unified_test", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, value in previous.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


class _Cursor:
    def __init__(self, row=None):
        self._row = row

    async def fetchone(self):
        return self._row


class _FakeDB:
    def __init__(self, cursor):
        self.cursor = cursor
        self.calls = []

    async def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return self.cursor


class UnifiedInboxIsolationTest(unittest.IsolatedAsyncioTestCase):
    async def test_unified_stats_joins_accounts_and_filters_user_uid(self):
        db = _load_db_module()
        self.assertTrue(
            hasattr(db, "get_unified_inbox_stats"),
            "get_unified_inbox_stats must be implemented",
        )
        fake = _FakeDB(_Cursor((12, 3)))

        with patch.object(db, "get_db", AsyncMock(return_value=fake)):
            result = await db.get_unified_inbox_stats(
                "user-a",
                ["account-1", "account-2"],
            )

        self.assertEqual(result, {"total_count": 12, "unread_count": 3})
        sql, params = fake.calls[0]
        normalized = " ".join(sql.lower().split())
        self.assertIn("join accounts", normalized)
        self.assertIn("user_uid", normalized)
        self.assertEqual(tuple(params), ("user-a", "account-1", "account-2"))


if __name__ == "__main__":
    unittest.main()
