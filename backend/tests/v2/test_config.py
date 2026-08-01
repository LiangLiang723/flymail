import inspect
import os
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from flymail.config import FlyMailSettings
from flymail.domain.enums import (
    AccountRuntimeStatus,
    BodyCacheState,
    JobStatus,
    ObjectKind,
    OperationStatus,
)
from flymail.domain.errors import (
    AuthorizationError,
    ConfigurationError,
    ConflictError,
    NotFoundError,
    PermanentError,
    RetryableError,
)
from flymail.domain.ids import new_id
from v2_dev import create_app
from v2_worker import run_worker


class FlyMailSettingsTests(unittest.TestCase):
    def valid_env(self) -> dict[str, str]:
        return {
            "DATABASE_URL": "mysql://flymail:distinct-password@127.0.0.1:3306/flymail_v2_test",
            "FLYMAIL_SESSION_SECRET": "distinct-session-secret-value",
            "FLYMAIL_DATA_DIR": "/tmp/flymail-v2-data",
        }

    def test_api_and_worker_use_distinct_pool_names_and_limits(self):
        with patch.dict(os.environ, self.valid_env(), clear=True):
            api = FlyMailSettings.from_env("api")
            worker = FlyMailSettings.from_env("worker")

        self.assertEqual((api.db_pool_name, api.db_min_connections, api.db_max_connections), ("flymail-api", 2, 12))
        self.assertEqual((worker.db_pool_name, worker.db_min_connections, worker.db_max_connections), ("flymail-worker", 2, 8))

    def test_settings_derive_object_directories_and_defaults(self):
        with patch.dict(os.environ, self.valid_env(), clear=True):
            settings = FlyMailSettings.from_env("api")

        self.assertEqual(settings.data_dir, Path("/tmp/flymail-v2-data"))
        self.assertEqual(settings.object_dir, Path("/tmp/flymail-v2-data/objects/sha256"))
        self.assertEqual(settings.object_tmp_dir, Path("/tmp/flymail-v2-data/objects/.tmp"))
        self.assertEqual(settings.worker_heartbeat_seconds, 10)
        self.assertEqual(settings.job_lease_seconds, 60)
        self.assertEqual(settings.default_body_quota_bytes, 5 * 1024**3)

    def test_short_session_secret_is_rejected(self):
        env = self.valid_env() | {"FLYMAIL_SESSION_SECRET": "short"}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(ValueError, "at least 16"):
                FlyMailSettings.from_env("api")

    def test_missing_database_url_is_rejected(self):
        env = self.valid_env()
        env.pop("DATABASE_URL")
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(ConfigurationError, "DATABASE_URL"):
                FlyMailSettings.from_env("api")

    def test_missing_data_directory_is_rejected(self):
        env = self.valid_env()
        env.pop("FLYMAIL_DATA_DIR")
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(ConfigurationError, "FLYMAIL_DATA_DIR"):
                FlyMailSettings.from_env("api")

    def test_invalid_process_role_is_rejected(self):
        with patch.dict(os.environ, self.valid_env(), clear=True):
            with self.assertRaisesRegex(ConfigurationError, "role"):
                FlyMailSettings.from_env("scheduler")  # type: ignore[arg-type]

    def test_repr_does_not_include_database_url_or_session_secret(self):
        env = self.valid_env()
        with patch.dict(os.environ, env, clear=True):
            settings = FlyMailSettings.from_env("api")

        rendered = repr(settings)
        self.assertNotIn(env["DATABASE_URL"], rendered)
        self.assertNotIn("distinct-password", rendered)
        self.assertNotIn(env["FLYMAIL_SESSION_SECRET"], rendered)


class DomainContractTests(unittest.TestCase):
    def test_generated_ids_include_normalized_prefix_and_are_unique(self):
        first = new_id(" UsR ")
        second = new_id("usr")

        self.assertTrue(first.startswith("usr_"))
        self.assertNotEqual(first, second)
        self.assertEqual(len(first), len("usr_") + 32)

    def test_invalid_id_prefix_is_rejected(self):
        for prefix in ("", "1user", "user-name", "a", "x" * 17):
            with self.subTest(prefix=prefix):
                with self.assertRaisesRegex(ValueError, "invalid id prefix"):
                    new_id(prefix)

    def test_shared_enums_use_exact_persisted_values(self):
        self.assertEqual([item.value for item in JobStatus], [
            "pending", "leased", "running", "succeeded", "retry_wait", "failed", "cancelled",
        ])
        self.assertEqual([item.value for item in OperationStatus], [
            "pending", "applying", "synced", "retry_wait", "review_required", "conflict", "failed", "cancelled",
        ])
        self.assertEqual([item.value for item in BodyCacheState], [
            "not_requested", "queued", "fetching", "ready", "evicted", "failed", "unavailable",
        ])
        self.assertEqual([item.value for item in ObjectKind], [
            "body_html", "body_text", "inline_image", "attachment", "raw_eml", "draft_attachment",
            "user_avatar", "account_icon", "contact_avatar", "notification_asset",
        ])
        self.assertEqual([item.value for item in AccountRuntimeStatus], [
            "active", "normal", "quiet", "degraded", "auth_required", "disabled",
        ])

    def test_domain_errors_are_catchable_as_configuration_or_runtime_failures(self):
        error_types = (
            ConfigurationError,
            NotFoundError,
            ConflictError,
            AuthorizationError,
            RetryableError,
            PermanentError,
        )
        for error_type in error_types:
            with self.subTest(error_type=error_type.__name__):
                self.assertTrue(issubclass(error_type, Exception))
                self.assertEqual(str(error_type("message")), "message")


class DevelopmentEntrypointTests(unittest.IsolatedAsyncioTestCase):
    async def test_v2_health_returns_api_role_and_repository_version(self):
        with patch.dict(os.environ, FlyMailSettingsTests().valid_env(), clear=True):
            settings = FlyMailSettings.from_env("api")
        expected = {
            "status": "ok",
            "role": "api",
            "schema_version": 7,
            "database": "ok",
            "object_store": "ok",
        }
        with patch("v2_dev.inspect_foundation_health", new=AsyncMock(return_value=expected)):
            transport = httpx.ASGITransport(app=create_app(settings))
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                response = await client.get("/api/v2/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), expected)

    async def test_v2_health_failure_is_safe_and_returns_service_unavailable(self):
        with patch.dict(os.environ, FlyMailSettingsTests().valid_env(), clear=True):
            settings = FlyMailSettings.from_env("api")
        expected = {
            "status": "error",
            "role": "api",
            "schema_version": 0,
            "database": "error",
            "object_store": "error",
        }
        with patch("v2_dev.inspect_foundation_health", new=AsyncMock(return_value=expected)):
            transport = httpx.ASGITransport(app=create_app(settings))
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                response = await client.get("/api/v2/health")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), expected)
        rendered = response.text
        self.assertNotIn(settings.database_url, rendered)
        self.assertNotIn(str(settings.data_dir), rendered)
        self.assertNotIn(settings.session_secret, rendered)

    async def test_worker_entrypoint_is_async_stoppable_and_has_no_placeholder_failure(self):
        self.assertTrue(inspect.iscoroutinefunction(run_worker))
        parameters = inspect.signature(run_worker).parameters
        self.assertIn("stop_event", parameters)
        self.assertIn("now_fn", parameters)
        source = inspect.getsource(run_worker).lower()
        self.assertIn("release_expired_leases", source)
        self.assertIn("asyncio.taskgroup", source)
        self.assertIn("await stop.wait()", source)
        self.assertIn("_release_worker_leases", source)
        self.assertNotIn("not implemented", source)


if __name__ == "__main__":
    unittest.main()
