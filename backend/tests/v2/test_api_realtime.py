from __future__ import annotations

import asyncio
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

import httpx

from flymail.application.auth import AuthenticatedSession
from flymail.application.realtime import RealtimeService
from flymail.config import FlyMailSettings
from flymail.domain.errors import AuthenticationError
from flymail.infrastructure.db.migrations.runner import run_migrations
from flymail.infrastructure.security.passwords import hash_password
from flymail.repositories.base import AdminContext, TenantContext
from flymail.repositories.realtime import RealtimeRepository
from flymail.repositories.users import UserRepository
from tests.v2.mysql_test_case import MySqlIsolatedAsyncioTestCase
from flymail.api.app import create_app


ORIGIN = "https://testserver"


class FakeWebSocket:
    def __init__(self, *, cookie: str = "cookie", after: str = "0", slow: bool = False) -> None:
        self.cookies = {"flymail_v2_session": cookie}
        self.query_params = {"after": after}
        self.headers = {"origin": ORIGIN, "host": "testserver"}
        self.url = type("Url", (), {"scheme": "https"})()
        self.accepted = False
        self.closed: tuple[int, str] | None = None
        self.sent: list[dict] = []
        self.slow = slow

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, value: dict) -> None:
        if self.slow:
            await asyncio.sleep(1)
        self.sent.append(value)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = (code, reason)


class SequenceAuth:
    def __init__(self, session: AuthenticatedSession, failures_after: int) -> None:
        self.session = session
        self.failures_after = failures_after
        self.calls = 0

    async def authenticate(self, _cookie: str) -> AuthenticatedSession:
        self.calls += 1
        if self.calls > self.failures_after:
            raise AuthenticationError("revoked")
        return self.session


class RealtimeApiTests(MySqlIsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await run_migrations(self.api_pool)
        await self._clear_tables()
        self.temp_dir = tempfile.TemporaryDirectory(prefix="flymail-v2-realtime-api-")
        root = Path(self.temp_dir.name)
        self.settings = FlyMailSettings(
            role="api",
            database_url=self.database_url(),
            data_dir=root,
            object_dir=root / "objects" / "sha256",
            object_tmp_dir=root / "objects" / ".tmp",
            session_secret="realtime-api-session-secret-0123456789abcdef",
            db_pool_name="flymail-api",
            db_min_connections=2,
            db_max_connections=12,
        )
        self.user = await self._create_user("realtime-user", "RealtimePassword!123")
        self.other = await self._create_user("realtime-other", "OtherPassword!123")

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()
        await super().asyncTearDown()

    async def _clear_tables(self) -> None:
        tables = (
            "realtime_events", "login_rate_limits", "user_sessions", "user_profiles",
            "user_settings", "users", "process_heartbeats",
        )
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                for table in tables:
                    await cursor.execute(f"DELETE FROM {table}")
            await connection.commit()

    async def _create_user(self, username: str, password: str):
        async with self.api_pool.acquire() as connection:
            await connection.begin()
            user = await UserRepository(connection).create_user_for_admin(
                AdminContext("usr_realtime_test_admin"),
                username=username,
                password_hash=hash_password(password),
            )
            await connection.commit()
        return user

    @asynccontextmanager
    async def running_app(self):
        app = create_app(self.settings)
        async with app.router.lifespan_context(app):
            yield app

    def client(self, app, source: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False, client=(source, 443)),
            base_url=ORIGIN,
        )

    async def login(self, client: httpx.AsyncClient, username: str, password: str):
        response = await client.post("/api/v2/auth/login", json={"username": username, "password": password})
        self.assertEqual(response.status_code, 200)
        return response

    async def append(self, user_uid: str, event_type: str, aggregate_id: str, payload: dict, *, now: float = 100.0, ttl: float = 1000.0) -> int:
        async with self.api_pool.acquire() as connection:
            await connection.begin()
            sequence = await RealtimeRepository(connection).append(
                TenantContext(user_uid),
                event_type=event_type,
                aggregate_type="thread",
                aggregate_id=aggregate_id,
                payload=payload,
                now=now,
                expires_at=now + ttl,
            )
            await connection.commit()
        return sequence

    async def test_http_events_are_monotonic_tenant_scoped_and_resume_after_cursor(self):
        first = await self.append(self.user.id, "thread.created", "thr_one", {"thread_id": "thr_one"})
        await self.append(self.other.id, "thread.created", "thr_secret", {"thread_id": "thr_secret"})
        third = await self.append(self.user.id, "thread.updated", "thr_two", {"thread_id": "thr_two"})
        async with self.running_app() as app:
            app.state.realtime_service.now_fn = lambda: 100.0
            async with self.client(app, "203.0.113.110") as client:
                await self.login(client, "realtime-user", "RealtimePassword!123")
                all_events = await client.get("/api/v2/events", params={"after": 0})
                resumed = await client.get("/api/v2/events", params={"after": first})
        self.assertEqual(all_events.status_code, 200)
        self.assertEqual([item["sequence"] for item in all_events.json()["events"]], [first, third])
        self.assertEqual([item["sequence"] for item in resumed.json()["events"]], [third])
        self.assertNotIn("thr_secret", all_events.text)
        self.assertEqual(all_events.headers["cache-control"], "no-store")

    async def test_expired_window_requires_resync_and_payload_is_bounded(self):
        first = await self.append(self.user.id, "thread.created", "thr_old", {"thread_id": "thr_old"}, now=10, ttl=5)
        second = await self.append(self.user.id, "thread.updated", "thr_expired", {"thread_id": "thr_expired"}, now=20, ttl=5)
        await self.append(self.user.id, "thread.updated", "thr_live", {"thread_id": "thr_live"}, now=100, ttl=1000)
        async with self.running_app() as app:
            app.state.now_fn = lambda: 200.0
            app.state.realtime_service.now_fn = lambda: 200.0
            async with self.client(app, "203.0.113.111") as client:
                await self.login(client, "realtime-user", "RealtimePassword!123")
                response = await client.get("/api/v2/events", params={"after": first})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "resync_required")
        self.assertIn("bootstrap", response.json()["error"]["details"]["scopes"])
        self.assertLess(first, second)
        async with self.api_pool.acquire() as connection:
            with self.assertRaises(ValueError):
                await RealtimeRepository(connection).append(
                    TenantContext(self.user.id),
                    event_type="message.body_state",
                    aggregate_type="message",
                    aggregate_id="msg_one",
                    payload={"body_html": "secret", "recipients": ["a@example.com"]},
                    now=1,
                    expires_at=10,
                )

    async def test_revoked_websocket_session_closes_after_backlog(self):
        sequence = await self.append(self.user.id, "thread.created", "thr_ws", {"thread_id": "thr_ws"})
        session = AuthenticatedSession(
            record=type("Record", (), {"id": "ses_ws"})(),
            user=self.user,
            csrf_token="csrf",
        )
        auth = SequenceAuth(session, failures_after=1)
        websocket = FakeWebSocket(after="0")
        service = RealtimeService(
            self.api_pool,
            auth_service=auth,
            allowed_origin=ORIGIN,
            wait_timeout=0.01,
            send_timeout=0.1,
            now_fn=lambda: 100,
        )
        await service.serve_websocket(websocket)
        self.assertTrue(websocket.accepted)
        self.assertEqual(websocket.sent[0]["events"][0]["sequence"], sequence)
        self.assertEqual(websocket.closed[0], 4401)

    async def test_slow_websocket_is_disconnected_without_blocking_publisher(self):
        await self.append(self.user.id, "thread.created", "thr_slow", {"thread_id": "thr_slow"})
        session = AuthenticatedSession(
            record=type("Record", (), {"id": "ses_slow"})(),
            user=self.user,
            csrf_token="csrf",
        )
        websocket = FakeWebSocket(slow=True)
        service = RealtimeService(
            self.api_pool,
            auth_service=SequenceAuth(session, failures_after=100),
            allowed_origin=ORIGIN,
            wait_timeout=0.01,
            send_timeout=0.01,
            now_fn=lambda: 100,
        )
        await asyncio.wait_for(service.serve_websocket(websocket), timeout=0.5)
        self.assertEqual(websocket.closed[0], 1013)

    async def test_event_types_are_exact_and_cleanup_keeps_recent_per_user(self):
        async with self.api_pool.acquire() as connection:
            repository = RealtimeRepository(connection)
            self.assertIn("version.changed", repository.EVENT_TYPES)
            self.assertIn("notification.created", repository.EVENT_TYPES)
            with self.assertRaises(ValueError):
                await repository.append(
                    TenantContext(self.user.id),
                    event_type="arbitrary.event",
                    aggregate_type="x",
                    aggregate_id="x",
                    payload={},
                    now=1,
                    expires_at=2,
                )
        for index in range(8):
            await self.append(
                self.user.id,
                "sync.updated",
                f"sync_{index}",
                {"account_id": f"acc_{index}"},
                now=100 + index,
                ttl=1000,
            )
        async with self.api_pool.acquire() as connection:
            await connection.begin()
            deleted = await RealtimeRepository(connection).cleanup(now=200, per_user_limit=3)
            await connection.commit()
        self.assertGreaterEqual(deleted, 5)
        self.assertEqual(
            await self.scalar("SELECT COUNT(*) FROM realtime_events WHERE user_uid=%s", (self.user.id,)),
            3,
        )
