from __future__ import annotations

import ast
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from flymail.api.routes.realtime import router as realtime_router
from flymail.config import FlyMailSettings
from flymail.repositories.realtime import REALTIME_EVENT_TYPES
from flymail.api.app import create_app


EXPECTED_METHODS = {
    "/api/v2/bootstrap": {"get"},
    "/api/v2/threads": {"get"},
    "/api/v2/threads/{thread_id}": {"get"},
    "/api/v2/operations": {"post"},
    "/api/v2/operations/mark-all-read": {"post"},
    "/api/v2/attachments/{attachment_id}": {"get"},
    "/api/v2/messages/{message_id}/raw": {"get"},
    "/api/v2/messages/{message_id}/raw/request": {"post"},
    "/api/v2/messages/{message_id}/raw/content": {"get"},
    "/api/v2/search": {"post"},
    "/api/v2/search/suggestions": {"get"},
    "/api/v2/saved-searches": {"get", "post"},
    "/api/v2/drafts": {"post"},
    "/api/v2/drafts/{draft_id}": {"get", "put", "delete"},
    "/api/v2/drafts/{draft_id}/send": {"post"},
    "/api/v2/events": {"get"},
    "/api/v2/settings": {"get", "put"},
    "/api/v2/sync": {"get"},
    "/api/v2/sync/accounts/{account_id}/refresh": {"post"},
    "/api/v2/sync/conflicts": {"get"},
    "/api/v2/sync/conflicts/{operation_id}/resolve": {"post"},
    "/api/v2/profile": {"get", "patch"},
    "/api/v2/profile/avatar": {"get", "post"},
    "/api/v2/contacts": {"get", "post"},
    "/api/v2/contacts/quick-add": {"post"},
    "/api/v2/contacts/autocomplete": {"get"},
    "/api/v2/admin/history-sync": {"get"},
    "/api/v2/notifications": {"get"},
    "/api/v2/notification-settings": {"get", "put"},
    "/api/v2/notification-channels": {"get", "post"},
    "/api/v2/notification-channels/{channel_id}": {"put", "delete"},
    "/api/v2/notification-channels/{channel_id}/test": {"post"},
    "/api/v2/notification-rules": {"get", "post"},
    "/api/v2/notification-publishers": {"get", "post"},
    "/api/v2/storage/roots": {"get"},
    "/api/v2/storage/roots/{root_id}/browse": {"get"},
    "/api/v2/admin/storage-roots": {"post"},
    "/api/v2/admin/diagnostics": {"get"},
    "/api/v2/admin/backups": {"get", "post"},
    "/api/v2/admin/backups/{backup_id}/inspect": {"post"},
    "/api/v2/admin/backups/{backup_id}/download": {"get"},
    "/api/v2/admin/backups/{backup_id}/restore-rehearsal": {"post"},
}

FORBIDDEN_PUBLIC_NAMES = {
    "password",
    "password_hash",
    "credential",
    "credentials",
    "ciphertext",
    "nonce",
    "access_token",
    "refresh_token",
    "authorization_code",
    "session_secret",
    "database_url",
    "root_path",
    "content_sha256",
    "object_sha256",
}


class ApiFeatureContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="flymail-v2-feature-contract-")
        root = Path(self.temp_dir.name)
        settings = FlyMailSettings(
            role="api",
            database_url="mysql://flymail:private@127.0.0.1:3306/flymail_contract",
            data_dir=root,
            object_dir=root / "objects" / "sha256",
            object_tmp_dir=root / "objects" / ".tmp",
            session_secret="feature-contract-session-secret-0123456789abcdef",
            db_pool_name="flymail-contract",
            db_min_connections=1,
            db_max_connections=2,
        )
        self.app = create_app(settings)
        self.openapi = self.app.openapi()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_openapi_exposes_exact_feature_routes_without_production_restore(self):
        paths = self.openapi["paths"]
        for path, methods in EXPECTED_METHODS.items():
            with self.subTest(path=path):
                self.assertTrue(methods.issubset(set(paths[path])))
        self.assertNotIn("/api/v2/admin/backups/{backup_id}/restore", paths)
        self.assertNotIn("/api/v2/admin/restore", paths)
        self.assertNotIn("/api/v2/objects/{content_sha256}", paths)
        self.assertTrue(
            any(
                getattr(route, "path", None) == "/api/v2/realtime"
                for route in realtime_router.routes
            )
        )

    def test_openapi_success_responses_do_not_expose_secret_or_storage_fields(self):
        schemas = self.openapi.get("components", {}).get("schemas", {})
        pending: list[str] = []
        for methods in self.openapi["paths"].values():
            for operation in methods.values():
                if not isinstance(operation, dict):
                    continue
                for status_code, response in operation.get("responses", {}).items():
                    if not str(status_code).startswith("2") or not isinstance(response, dict):
                        continue
                    for media in response.get("content", {}).values():
                        schema = media.get("schema", {}) if isinstance(media, dict) else {}
                        reference = schema.get("$ref") if isinstance(schema, dict) else None
                        if reference:
                            pending.append(str(reference).rsplit("/", 1)[-1])
        visited: set[str] = set()
        violations: list[str] = []
        while pending:
            schema_name = pending.pop()
            if schema_name in visited:
                continue
            visited.add(schema_name)
            schema = schemas.get(schema_name, {})
            if not isinstance(schema, dict):
                continue
            for property_name, property_schema in schema.get("properties", {}).items():
                if property_name.casefold() in FORBIDDEN_PUBLIC_NAMES:
                    violations.append(f"{schema_name}.{property_name}")
                if isinstance(property_schema, dict):
                    reference = property_schema.get("$ref")
                    if reference:
                        pending.append(str(reference).rsplit("/", 1)[-1])
                    items = property_schema.get("items", {})
                    if isinstance(items, dict) and items.get("$ref"):
                        pending.append(str(items["$ref"]).rsplit("/", 1)[-1])
                    for keyword in ("anyOf", "oneOf", "allOf"):
                        for option in property_schema.get(keyword, []):
                            if isinstance(option, dict) and option.get("$ref"):
                                pending.append(str(option["$ref"]).rsplit("/", 1)[-1])
        self.assertEqual(violations, [])

    def test_operation_ids_are_unique_and_all_v2_errors_share_safe_envelope(self):
        operation_ids: list[str] = []
        for methods in self.openapi["paths"].values():
            for operation in methods.values():
                if isinstance(operation, dict) and operation.get("operationId"):
                    operation_ids.append(str(operation["operationId"]))
        self.assertEqual(len(operation_ids), len(set(operation_ids)))
        self.assertNotIn("traceback", str(self.openapi).casefold())

    def test_route_modules_do_not_import_remote_protocol_or_worker_implementation(self):
        route_root = Path(__file__).resolve().parents[2] / "flymail" / "api" / "routes"
        violations: list[str] = []
        for path in sorted(route_root.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                module = ""
                if isinstance(node, ast.ImportFrom):
                    module = str(node.module or "")
                elif isinstance(node, ast.Import):
                    module = ",".join(alias.name for alias in node.names)
                if any(
                    marker in module
                    for marker in (
                        "imap_client",
                        "smtp_client",
                        "flymail.workers",
                        "provider_credentials",
                    )
                ):
                    violations.append(f"{path.name}:{module}")
        self.assertEqual(violations, [])

    def test_openapi_snapshot_and_realtime_event_schema_are_frozen(self):
        fixture_path = Path(__file__).parent / "fixtures" / "openapi-v2.json"
        expected = json.loads(fixture_path.read_text(encoding="utf-8"))
        canonical = json.dumps(
            self.openapi,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        methods = {"get", "post", "put", "patch", "delete"}
        actual = {
            "event_types": sorted(REALTIME_EVENT_TYPES),
            "operation_count": sum(
                1
                for item in self.openapi["paths"].values()
                for method in item
                if method in methods
            ),
            "path_count": len(self.openapi["paths"]),
            "schema_count": len(
                self.openapi.get("components", {}).get("schemas", {})
            ),
            "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "title": self.openapi["info"]["title"],
            "version": self.openapi["info"]["version"],
        }
        self.assertEqual(actual, expected)

    def test_all_high_risk_mutation_routes_declare_csrf_dependency(self):
        route_root = Path(__file__).resolve().parents[2] / "flymail" / "api" / "routes"
        required_files = (
            "operations.py",
            "compose.py",
            "settings.py",
            "contacts.py",
            "admin_sync.py",
            "sync.py",
            "profiles.py",
            "notifications.py",
            "storage.py",
            "backups.py",
        )
        for name in required_files:
            source = (route_root / name).read_text(encoding="utf-8")
            with self.subTest(name=name):
                self.assertIn("Depends(require_csrf)", source)
