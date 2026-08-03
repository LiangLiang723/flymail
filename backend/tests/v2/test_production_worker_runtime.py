"""Formal Worker runtime wiring and production handler contracts."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from flymail.config import FlyMailSettings
from flymail.infrastructure.db.pool import DatabasePool
from flymail.providers.core.smtp_client import (
    SentAppendResult,
    SentVerificationResult,
    SmtpSendResult,
)
from flymail.workers.dispatcher import JobContext, JobOutcome, WorkerDispatcher
from flymail.workers.main import WORKER_JOB_KINDS


class FakeProviderRuntime:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def verify(self, **kwargs) -> None:
        self.calls.append(("verify", kwargs))

    async def cleanup(self, **kwargs) -> None:
        self.calls.append(("cleanup", kwargs))

    def stream(self, locator, fetch_spec):
        self.calls.append(("stream", (locator, fetch_spec)))

        async def chunks():
            yield b"payload"

        return chunks()

    async def observe(self, operation):
        self.calls.append(("observe", operation))
        return None

    async def apply(self, command):
        self.calls.append(("apply", command))
        raise AssertionError("not used by wiring test")

    async def send(self, request):
        self.calls.append(("send", request))
        return SmtpSendResult(250, "accepted")

    async def verify_sent(self, request):
        self.calls.append(("verify_sent", request))
        return SentVerificationResult(False)

    async def append_sent_copy(self, request):
        self.calls.append(("append_sent_copy", request))
        return SentAppendResult()

    async def synchronize(
        self,
        context: JobContext,
        payload,
        *,
        job_kind: str,
    ) -> JobOutcome:
        self.calls.append(("synchronize", (context, dict(payload), job_kind)))
        return JobOutcome.success()


class FakeContentService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def fetch_body(self, _tenant, message_id: str):
        self.calls.append(("body", message_id))

    async def fetch_inline(self, _tenant, attachment_id: str):
        self.calls.append(("inline", attachment_id))

    async def fetch_attachment(self, _tenant, attachment_id: str, *, supports_partial: bool):
        self.calls.append(("attachment", f"{attachment_id}:{supports_partial}"))

    async def fetch_raw_eml(self, _tenant, message_id: str):
        self.calls.append(("raw_eml", message_id))


class _FakeConnection:
    async def begin(self) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


class _FakeLease:
    def __init__(self, connection: _FakeConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> _FakeConnection:
        return self.connection

    async def __aexit__(self, _exc_type, _exc, _traceback) -> None:
        return None


class _FakePool:
    def __init__(self) -> None:
        self.connection = _FakeConnection()
        self.closed = False

    def acquire(self) -> _FakeLease:
        return _FakeLease(self.connection)

    async def close(self) -> None:
        self.closed = True


class ProductionWorkerRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="flymail-v2-worker-runtime-")
        root = Path(self.temp_dir.name)
        self.settings = FlyMailSettings(
            role="worker",
            database_url="mysql://user:password@127.0.0.1:3306/flymail_worker_test",
            data_dir=root,
            object_dir=root / "objects" / "sha256",
            object_tmp_dir=root / "objects" / ".tmp",
            session_secret="production-worker-runtime-test-secret",
            db_pool_name="worker-runtime-test",
            db_min_connections=1,
            db_max_connections=2,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def context(self) -> JobContext:
        return JobContext(
            job_id="job_runtime_1",
            user_uid="usr_runtime_1",
            account_id="acc_runtime_1",
            provider_key="generic",
            queue_name="interactive",
            worker_id="wrk_runtime_1",
            attempt_count=1,
            stop_event=asyncio.Event(),
        )

    def test_production_builder_registers_exact_job_kinds(self):
        from flymail.workers.runtime import build_production_worker_dispatcher

        pool = DatabasePool.__new__(DatabasePool)
        dispatcher = build_production_worker_dispatcher(
            pool,
            self.settings,
            provider_runtime=FakeProviderRuntime(),
        )
        self.assertIsInstance(dispatcher, WorkerDispatcher)
        self.assertEqual(dispatcher.registered_kinds, tuple(sorted(WORKER_JOB_KINDS)))

    async def test_content_handler_routes_exact_content_kind(self):
        from flymail.workers.runtime import ContentJobHandler

        service = FakeContentService()
        context = self.context()
        scenarios = (
            ("content.body", {"message_id": "msg_1"}, ("body", "msg_1")),
            ("content.inline", {"attachment_id": "att_1"}, ("inline", "att_1")),
            (
                "content.attachment",
                {"attachment_id": "att_2"},
                ("attachment", "att_2:True"),
            ),
            ("content.raw_eml", {"message_id": "msg_2"}, ("raw_eml", "msg_2")),
        )
        for job_kind, payload, expected in scenarios:
            outcome = await ContentJobHandler(service, job_kind)(context, payload)
            self.assertEqual(outcome.action, "complete")
            self.assertEqual(service.calls[-1], expected)

    async def test_sync_handler_delegates_kind_and_secret_free_payload(self):
        from flymail.workers.runtime import SyncJobHandler

        runtime = FakeProviderRuntime()
        context = self.context()
        payload = {"account_id": "acc_runtime_1", "mailbox_id": "mbx_runtime_1"}
        for job_kind in (
            "sync.incremental",
            "sync.initial",
            "sync.mailbox_refresh",
            "sync.reconcile",
        ):
            outcome = await SyncJobHandler(runtime, job_kind)(context, payload)
            self.assertEqual(outcome.action, "complete")
            name, (_context, recorded, recorded_kind) = runtime.calls[-1]
            self.assertEqual(name, "synchronize")
            self.assertEqual(recorded, payload)
            self.assertEqual(recorded_kind, job_kind)

    async def test_default_worker_uses_production_dispatcher_builder(self):
        from flymail.workers import main as worker_main

        dispatcher = WorkerDispatcher()
        for kind in WORKER_JOB_KINDS:
            dispatcher.register(kind, lambda _context, _payload: asyncio.sleep(0, result=JobOutcome.success()))

        fake_pool = _FakePool()
        stop = asyncio.Event()
        stop.set()
        with (
            mock.patch.object(worker_main.FlyMailSettings, "from_env", return_value=self.settings),
            mock.patch.object(worker_main.DatabasePool, "create", new=mock.AsyncMock(return_value=fake_pool)),
            mock.patch.object(worker_main, "run_migrations", new=mock.AsyncMock()),
            mock.patch.object(worker_main, "build_production_worker_dispatcher", return_value=dispatcher) as builder,
            mock.patch.object(worker_main.JobRepository, "release_expired_leases", new=mock.AsyncMock(return_value=0)),
            mock.patch.object(worker_main, "validate_worker_job_registry", new=mock.AsyncMock()),
            mock.patch.object(worker_main, "_release_worker_leases", new=mock.AsyncMock()),
        ):
            await worker_main.run_worker(stop_event=stop)
        builder.assert_called_once_with(fake_pool, self.settings)


if __name__ == "__main__":
    unittest.main()
