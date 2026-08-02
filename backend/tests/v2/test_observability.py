from __future__ import annotations

import io
import json
import logging
import tempfile
import unittest
from pathlib import Path

from flymail.api.app import create_app
from flymail.api.middleware import RequestContextMiddleware
from flymail.config import FlyMailSettings
from flymail.observability.logging import SafeJsonFormatter, get_safe_logger
from flymail.observability.metrics import JobTiming
from flymail.observability.timing import RequestTiming


class ObservabilityUnitTests(unittest.TestCase):
    def test_safe_logger_redacts_secrets_urls_message_content_and_filenames(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(SafeJsonFormatter())
        logger = get_safe_logger("observability-test", handlers=[handler])
        logger.info(
            "worker event",
            request_id="req-safe-1234",
            job_id="job-1",
            provider="gmail",
            operation="account.verify",
            database_url="mysql://flymail:p@ss\\word@127.0.0.1:3306/flymail",
            password="db-password",
            authorization_code="mail-auth-code",
            access_token="oauth-access-token",
            refresh_token="oauth-refresh-token",
            session_secret="session-secret-value",
            cookie="flymail_v2_session=signed-cookie",
            body="private message body",
            attachment_filename="payroll.pdf",
            error_class="ProviderUnavailable",
            duration_ms=12.5,
        )
        rendered = stream.getvalue()
        payload = json.loads(rendered)
        self.assertEqual(payload["component"], "observability-test")
        self.assertEqual(payload["operation"], "account.verify")
        self.assertEqual(payload["error_class"], "ProviderUnavailable")
        for forbidden in (
            "p@ss\\word",
            "db-password",
            "mail-auth-code",
            "oauth-access-token",
            "oauth-refresh-token",
            "session-secret-value",
            "signed-cookie",
            "private message body",
            "payroll.pdf",
        ):
            self.assertNotIn(forbidden, rendered)
        for forbidden_key in (
            "database_url",
            "password",
            "authorization_code",
            "access_token",
            "refresh_token",
            "session_secret",
            "cookie",
            "body",
            "attachment_filename",
        ):
            self.assertNotIn(forbidden_key, payload)

    def test_request_and_job_timing_are_bounded_content_free_and_serializable(self):
        values = iter((10.0, 10.030))
        request = RequestTiming(perf_counter=lambda: next(values))
        request.record_db(10.0)
        request.record_object(5.0)
        request.record_serialize(5.0)
        snapshot = request.finish()
        self.assertEqual(snapshot["total_ms"], 30.0)
        self.assertEqual(snapshot["db_ms"], 10.0)
        self.assertEqual(snapshot["object_ms"], 5.0)
        self.assertEqual(snapshot["serialize_ms"], 5.0)
        self.assertIn("object;dur=5.000", request.server_timing())

        job = JobTiming(queue_wait_ms=25.0, retries=2)
        job.add_bytes_in(1024)
        job.add_bytes_out(2048)
        job.add_results(3)
        job.record_execution(15.5)
        self.assertEqual(
            job.snapshot(),
            {
                "queue_wait_ms": 25.0,
                "execution_ms": 15.5,
                "retries": 2,
                "bytes_in": 1024,
                "bytes_out": 2048,
                "result_count": 3,
            },
        )
        self.assertNotIn("body", json.dumps(job.snapshot()))

    def test_formal_health_alias_and_admin_diagnostics_routes_exist(self):
        with tempfile.TemporaryDirectory(prefix="flymail-observability-") as value:
            root = Path(value)
            app = create_app(
                FlyMailSettings(
                    role="api",
                    database_url="mysql://flymail:redacted@127.0.0.1:3306/flymail_test",
                    data_dir=root,
                    object_dir=root / "objects" / "sha256",
                    object_tmp_dir=root / "objects" / ".tmp",
                    session_secret="observability-session-secret-0123456789",
                    db_pool_name="flymail-api",
                    db_min_connections=2,
                    db_max_connections=12,
                )
            )
        paths = app.openapi()["paths"]
        self.assertIn("/api/health", paths)
        self.assertIn("/api/v2/health", paths)
        self.assertIn("/api/v2/admin/diagnostics", paths)

    def test_middleware_declares_db_object_and_serialize_server_timing(self):
        source = Path(RequestContextMiddleware.__module__.replace(".", "/") + ".py")
        project_root = Path(__file__).resolve().parents[2]
        rendered = (project_root / source).read_text(encoding="utf-8")
        self.assertIn("RequestTiming", rendered)
        self.assertIn("object;dur=", rendered)
        self.assertIn("serialize;dur=", rendered)


if __name__ == "__main__":
    unittest.main()
