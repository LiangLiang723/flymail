import importlib.util
import sys
import types
import unittest
from pathlib import Path
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
        spec = importlib.util.spec_from_file_location("db_for_contact_candidates_test", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, value in previous.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


class ContactCandidateAggregationTest(unittest.TestCase):
    def test_aggregates_correspondents_and_excludes_self_and_existing_contacts(self):
        db = _load_db_module()
        aggregate = getattr(db, "_aggregate_contact_candidates", None)
        self.assertIsNotNone(aggregate, "contact candidate aggregation must be implemented")

        rows = [
            (
                "Alice Zhang <alice@example.com>",
                "Me <me@example.com>",
                "",
                "2026-08-01T10:00:00+08:00",
            ),
            (
                "Me <me@example.com>",
                "Alice Zhang <alice@example.com>, Bob <bob@example.com>",
                "Carol <carol@example.com>",
                "2026-08-03T10:00:00+08:00",
            ),
            (
                "Saved Person <saved@example.com>",
                "Me <me@example.com>",
                "",
                "2026-08-04T10:00:00+08:00",
            ),
        ]

        result = aggregate(
            rows,
            own_email="me@example.com",
            existing_emails={"saved@example.com"},
            search="",
            limit=50,
        )

        by_email = {item["email"]: item for item in result}
        self.assertEqual(set(by_email), {"alice@example.com", "bob@example.com", "carol@example.com"})
        self.assertEqual(by_email["alice@example.com"]["name"], "Alice Zhang")
        self.assertEqual(by_email["alice@example.com"]["received_count"], 1)
        self.assertEqual(by_email["alice@example.com"]["sent_count"], 1)
        self.assertEqual(by_email["alice@example.com"]["total_count"], 2)
        self.assertEqual(by_email["alice@example.com"]["last_date"], "2026-08-03T10:00:00+08:00")
        self.assertNotIn("me@example.com", by_email)
        self.assertNotIn("saved@example.com", by_email)

    def test_search_and_limit_are_applied_after_aggregation(self):
        db = _load_db_module()
        rows = [
            ("Alice <alice@example.com>", "me@example.com", "", "2026-08-01"),
            ("Alina <alina@example.com>", "me@example.com", "", "2026-08-02"),
            ("Bob <bob@example.com>", "me@example.com", "", "2026-08-03"),
        ]

        result = db._aggregate_contact_candidates(
            rows,
            own_email="me@example.com",
            existing_emails=set(),
            search="ali",
            limit=1,
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["email"], "alina@example.com")


if __name__ == "__main__":
    unittest.main()
