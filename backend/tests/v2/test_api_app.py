from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import Query, Request

from flymail.config import FlyMailSettings
from flymail.domain.errors import AuthorizationError, ConflictError, NotFoundError
from flymail.infrastructure.db.migrations.runner import LATEST_SCHEMA_VERSION, run_migrations
from flymail.infrastructure.db.pool import DatabasePool
from flymail.workers.lease import WorkerHeartbeatService
from tests.v2.mysql_test_case import MySqlIsolatedAsyncioTestCase
from flymail.api.app import create_app


class ApiApplicationTests(MySqlIsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await run_migrations(self.api_pool)
        self.temp_dir = tempfile.TemporaryDirectory(prefix="flymail-v2-api-")
        root = Path(self.temp_dir.name)
        self.settings = FlyMailSettings(
            role="api",
            database_url=self.database_url(),
            data_dir=root,
            object_dir=root / "objects" / "sha256",
            object_tmp_dir=root / "objects" / ".tmp",
            session_secret="api-app-test-session-secret",
            db_pool_name="flymail-api",
            db_min_connections=2,
            db_max_connections=12,
        )
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT COUNT(*)
                    FROM information_schema.tables
                    WHERE table_schema = DATABASE()
                      AND table_name = 'process_heartbeats'
                    """
                )
                if int((await cursor.fetchone())[0] or 0) > 0:
                    await cursor.execute("DELETE FROM process_heartbeats")
                    await connection.commit()

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()
        await super().asyncTearDown()

    @asynccontextmanager
    async def app_client(self, app, *, now: float = 1_000.0, uptime: float = 0.0):
        async with app.router.lifespan_context(app):
            app.state.now_fn = lambda: now
            app.state.started_at = now - uptime
            transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                yield client

    async def touch_worker(self, timestamp: float) -> None:
        service = WorkerHeartbeatService(
            self.worker_pool,
            now_fn=lambda: timestamp,
            lease_seconds=60,
        )
        await service.touch("wrk_api_health", "worker")

    async def test_idle_worker_heartbeat_is_persisted_without_active_jobs(self):
        await self.touch_worker(900.0)
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT role, heartbeat_at
                    FROM process_heartbeats
                    WHERE process_id = 'wrk_api_health'
                    """
                )
                row = await cursor.fetchone()
        self.assertEqual(row, ("worker", 900.0))
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM worker_jobs WHERE lease_owner = 'wrk_api_health'"
            ),
            0,
        )

    async def test_health_reports_current_worker_schema_database_and_object_store(self):
        app = create_app(self.settings)
        async with self.app_client(app, now=1_000.0, uptime=120.0) as client:
            await self.touch_worker(995.0)
            response = await client.get("/api/v2/health")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["api"], "ok")
        self.assertEqual(payload["database"], "ok")
        self.assertEqual(payload["schema"], "ok")
        self.assertEqual(payload["schema_version"], LATEST_SCHEMA_VERSION)
        self.assertEqual(payload["expected_schema_version"], LATEST_SCHEMA_VERSION)
        self.assertEqual(payload["worker"], "ok")
        self.assertEqual(payload["worker_heartbeat_at"], 995.0)
        self.assertEqual(payload["object_store"], "ok")
        self.assertNotIn(self.settings.database_url, response.text)
        self.assertNotIn(self.settings.session_secret, response.text)
        self.assertNotIn(str(self.settings.data_dir), response.text)

    async def test_frontend_root_assets_and_history_routes_are_served_without_masking_api_404(self):
        ui_dir = Path(self.temp_dir.name) / "ui"
        asset_dir = ui_dir / "assets"
        asset_dir.mkdir(parents=True)
        (ui_dir / "index.html").write_text(
            "<!doctype html><html><body>FlyMail UI</body></html>",
            encoding="utf-8",
        )
        (asset_dir / "app.js").write_text(
            "window.flymailLoaded = true;",
            encoding="utf-8",
        )

        with patch.dict(os.environ, {"FLYMAIL_UI_DIR": str(ui_dir)}):
            app = create_app(self.settings)

        async with self.app_client(app) as client:
            root_response = await client.get("/")
            asset_response = await client.get("/assets/app.js")
            history_response = await client.get("/settings/accounts")
            unknown_api_response = await client.get("/api/v2/does-not-exist")
            missing_asset_response = await client.get("/assets/missing.js")

        self.assertEqual(root_response.status_code, 200)
        self.assertIn("text/html", root_response.headers["content-type"])
        self.assertIn("FlyMail UI", root_response.text)
        self.assertEqual(asset_response.status_code, 200)
        self.assertIn("javascript", asset_response.headers["content-type"])
        self.assertIn("flymailLoaded", asset_response.text)
        self.assertEqual(history_response.status_code, 200)
        self.assertIn("FlyMail UI", history_response.text)
        self.assertEqual(unknown_api_response.status_code, 404)
        self.assertEqual(unknown_api_response.json()["error"]["code"], "not_found")
        self.assertEqual(missing_asset_response.status_code, 404)
        self.assertEqual(missing_asset_response.json()["error"]["code"], "not_found")

    async def test_stale_worker_is_degraded_during_startup_then_unhealthy(self):
        await self.touch_worker(800.0)

        grace_app = create_app(self.settings)
        async with self.app_client(grace_app, now=1_000.0, uptime=10.0) as client:
            response = await client.get("/api/v2/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "degraded")
        self.assertEqual(response.json()["worker"], "stale")

        expired_app = create_app(self.settings)
        async with self.app_client(expired_app, now=1_000.0, uptime=600.0) as client:
            response = await client.get("/api/v2/health")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "error")
        self.assertEqual(response.json()["worker"], "stale")

    async def test_version_context_request_id_and_server_timing_are_stable(self):
        app = create_app(self.settings)

        @app.get("/api/v2/_test/context")
        async def context(request: Request):
            value = request.state.context
            return {
                "request_id": value.request_id,
                "trace_id": value.trace_id,
                "actor": value.actor,
            }

        async with self.app_client(app) as client:
            accepted = await client.get(
                "/api/v2/_test/context",
                headers={"X-Request-ID": "request-client_123"},
            )
            replaced = await client.get(
                "/api/v2/version",
                headers={"X-Request-ID": "unsafe request id"},
            )

        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.headers["X-Request-ID"], "request-client_123")
        self.assertEqual(accepted.json()["request_id"], "request-client_123")
        self.assertTrue(accepted.json()["trace_id"].startswith("trc_"))
        self.assertIsNone(accepted.json()["actor"])
        self.assertIn("total;dur=", accepted.headers["Server-Timing"])
        self.assertIn("db;dur=", accepted.headers["Server-Timing"])
        self.assertIn("object;dur=", accepted.headers["Server-Timing"])
        self.assertIn("serialize;dur=", accepted.headers["Server-Timing"])

        generated = replaced.headers["X-Request-ID"]
        self.assertTrue(generated.startswith("req_"))
        self.assertNotEqual(generated, "unsafe request id")
        self.assertEqual(replaced.json()["version"], app.version)
        self.assertEqual(
            replaced.json()["schema_version"],
            LATEST_SCHEMA_VERSION,
        )

    async def test_domain_validation_http_and_unexpected_errors_use_safe_envelope(self):
        app = create_app(self.settings)

        @app.get("/api/v2/_test/authorization")
        async def authorization():
            raise AuthorizationError("authorization-secret")

        @app.get("/api/v2/_test/conflict")
        async def conflict():
            raise ConflictError("conflict-secret")

        @app.get("/api/v2/_test/missing")
        async def missing():
            raise NotFoundError("missing-secret")

        @app.get("/api/v2/_test/validation")
        async def validation(count: int = Query(gt=0)):
            return {"count": count}

        @app.get("/api/v2/_test/unexpected")
        async def unexpected():
            raise RuntimeError(
                f"{self.settings.database_url} {self.settings.session_secret}"
            )

        async with self.app_client(app) as client:
            authorization_response = await client.get("/api/v2/_test/authorization")
            conflict_response = await client.get("/api/v2/_test/conflict")
            missing_response = await client.get("/api/v2/_test/missing")
            validation_response = await client.get(
                "/api/v2/_test/validation",
                params={"count": "private-input"},
            )
            unknown_response = await client.get("/api/v2/does-not-exist")
            method_response = await client.post("/api/v2/version")
            with self.assertLogs("flymail.v2.api", level="ERROR") as captured:
                unexpected_response = await client.get("/api/v2/_test/unexpected")

        cases = (
            (authorization_response, 403, "authorization_denied"),
            (conflict_response, 409, "conflict"),
            (missing_response, 404, "not_found"),
            (validation_response, 422, "validation_error"),
            (unknown_response, 404, "not_found"),
            (method_response, 405, "method_not_allowed"),
            (unexpected_response, 500, "internal_error"),
        )
        for response, status, code in cases:
            with self.subTest(code=code):
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.json()["error"]["code"], code)
                self.assertEqual(
                    response.json()["error"]["request_id"],
                    response.headers["X-Request-ID"],
                )
                self.assertIn("details", response.json()["error"])
                self.assertIn("Server-Timing", response.headers)

        rendered = "\n".join(
            response.text for response, _status, _code in cases
        ) + "\n" + "\n".join(captured.output)
        for forbidden in (
            self.settings.database_url,
            self.settings.session_secret,
            "authorization-secret",
            "conflict-secret",
            "missing-secret",
            "private-input",
        ):
            self.assertNotIn(forbidden, rendered)

    async def test_lifespan_closes_database_pool(self):
        app = create_app(self.settings)
        async with self.app_client(app):
            pool = app.state.database_pool
            self.assertFalse(pool.closed)
        self.assertTrue(pool.closed)

    async def test_startup_failure_closes_created_database_pool(self):
        app = create_app(self.settings)
        pool = await DatabasePool.create(self.settings)
        with patch(
            "flymail.api.app.DatabasePool.create",
            new=AsyncMock(return_value=pool),
        ), patch(
            "flymail.api.app._probe_object_store",
            side_effect=OSError("object store unavailable"),
        ):
            with self.assertRaises(OSError):
                async with app.router.lifespan_context(app):
                    pass
        self.assertTrue(pool.closed)

    async def test_realtime_close_failure_still_closes_database_pool(self):
        class BrokenRealtimeManager:
            async def close(self):
                raise RuntimeError("realtime close failed")

        app = create_app(self.settings)
        with self.assertRaises(RuntimeError):
            async with app.router.lifespan_context(app):
                pool = app.state.database_pool
                app.state.realtime_manager = BrokenRealtimeManager()
        self.assertTrue(pool.closed)


if __name__ == "__main__":
    unittest.main()
