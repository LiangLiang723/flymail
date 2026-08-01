from __future__ import annotations

import asyncio
import io
import logging
import time
import unittest
from collections.abc import AsyncIterator

from flymail.providers.contracts import ServiceEndpoint, TransportSecurity
from flymail.providers.core.imap_commands import (
    IdleEvent,
    ImapCommand,
    ImapCredentials,
    ImapResponse,
)
from flymail.providers.core.imap_session import ImapSession, ImapSessionState
from flymail.providers.core.rate_limit import AccountConnectionLimiter
from flymail.providers.errors import ProviderError, ProviderErrorCode
from flymail.providers.registry import ProviderRegistry


class FakeImapTransport:
    def __init__(self) -> None:
        self.connect_response = ImapResponse("OK", data="authenticated")
        self.responses: dict[str, list[ImapResponse]] = {
            "CAPABILITY": [
                ImapResponse(
                    "OK",
                    data=[b"* CAPABILITY IMAP4rev1 IDLE MOVE UIDPLUS CONDSTORE QRESYNC"],
                )
            ]
        }
        self.command_delay = 0.0
        self.block_event: asyncio.Event | None = None
        self.started = asyncio.Event()
        self.records: list[tuple[str, str]] = []
        self.active_commands = 0
        self.max_active_commands = 0
        self.close_count = 0
        self.connected_secret = ""
        self.connected_proxy = ""
        self.idle_source: AsyncIterator[IdleEvent] | None = None

    async def connect(self, credentials, endpoint, proxy) -> ImapResponse:
        self.connected_secret = credentials.secret
        self.connected_proxy = str(proxy or "")
        return self.connect_response

    async def execute(self, command_name: str, arguments: tuple[object, ...]) -> ImapResponse:
        normalized = command_name.strip().upper()
        self.records.append(("start", normalized))
        self.active_commands += 1
        self.max_active_commands = max(self.max_active_commands, self.active_commands)
        self.started.set()
        try:
            if self.block_event is not None:
                await self.block_event.wait()
            if self.command_delay:
                await asyncio.sleep(self.command_delay)
            queue = self.responses.get(normalized)
            if queue:
                return queue.pop(0)
            return ImapResponse("OK", data={"command": normalized, "arguments": arguments})
        finally:
            self.active_commands -= 1
            self.records.append(("end", normalized))

    def idle(self, mailbox_native_key: str) -> AsyncIterator[IdleEvent]:
        if self.idle_source is None:
            async def empty() -> AsyncIterator[IdleEvent]:
                if False:
                    yield IdleEvent("exists")
            return empty()
        return self.idle_source

    async def close(self) -> None:
        self.close_count += 1


class ImapSessionTests(unittest.IsolatedAsyncioTestCase):
    endpoint = ServiceEndpoint("imap.example.com", 993, TransportSecurity.TLS)

    async def connect_session(
        self,
        *,
        provider: str = "gmail",
        transport: FakeImapTransport | None = None,
        secret: str = "mail-password-secret",
    ) -> tuple[ImapSession, FakeImapTransport]:
        fake = transport or FakeImapTransport()
        session = ImapSession(ProviderRegistry.default().get(provider), fake)
        returned = await session.connect(
            ImapCredentials("user@example.com", secret, auth_kind="password"),
            self.endpoint,
            proxy="http://proxy-user:proxy-secret@proxy.example:8080",
        )
        self.assertIs(returned, session)
        return session, fake

    async def test_two_concurrent_execute_calls_are_serialized(self):
        session, transport = await self.connect_session()
        transport.command_delay = 0.03
        command = ImapCommand.identity("NOOP", timeout_seconds=1)

        first, second = await asyncio.gather(
            session.execute(command),
            session.execute(command),
        )

        self.assertEqual(first.status, "OK")
        self.assertEqual(second.status, "OK")
        self.assertEqual(transport.max_active_commands, 1)
        self.assertEqual(
            transport.records[-4:],
            [("start", "NOOP"), ("end", "NOOP"), ("start", "NOOP"), ("end", "NOOP")],
        )

    async def test_cancellation_releases_lock_and_closes_ambiguous_session(self):
        session, transport = await self.connect_session()
        transport.started.clear()
        transport.block_event = asyncio.Event()
        task = asyncio.create_task(
            session.execute(ImapCommand.identity("FETCH", timeout_seconds=30))
        )
        await asyncio.wait_for(transport.started.wait(), timeout=1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertEqual(session.state, ImapSessionState.FAILED)
        self.assertEqual(transport.close_count, 1)
        await asyncio.wait_for(session.disconnect(), timeout=1)
        self.assertEqual(session.state, ImapSessionState.DISCONNECTED)

    async def test_timeout_marks_failed_and_returns_retryable_connection_error(self):
        session, transport = await self.connect_session()
        transport.block_event = asyncio.Event()

        with self.assertRaises(ProviderError) as captured:
            await session.execute(ImapCommand.identity("FETCH", timeout_seconds=0.01))

        self.assertEqual(captured.exception.code, ProviderErrorCode.CONNECTION_FAILED)
        self.assertTrue(captured.exception.retryable)
        self.assertEqual(session.state, ImapSessionState.FAILED)
        self.assertEqual(transport.close_count, 1)

    async def test_bye_marks_failed_and_rejects_later_commands(self):
        session, transport = await self.connect_session()
        transport.responses["NOOP"] = [ImapResponse("BYE", text="server closing connection")]

        with self.assertRaises(ProviderError) as captured:
            await session.execute(ImapCommand.identity("NOOP", timeout_seconds=1))
        self.assertEqual(captured.exception.code, ProviderErrorCode.CONNECTION_FAILED)
        self.assertEqual(session.state, ImapSessionState.FAILED)
        self.assertEqual(transport.close_count, 1)

        with self.assertRaises(ProviderError) as later:
            await session.execute(ImapCommand.identity("NOOP", timeout_seconds=1))
        self.assertEqual(later.exception.code, ProviderErrorCode.CONNECTION_FAILED)
        self.assertEqual(transport.records.count(("start", "NOOP")), 1)

    async def test_switching_mailbox_updates_selected_state_only_after_success(self):
        session, transport = await self.connect_session()
        transport.responses["SELECT"] = [
            ImapResponse(
                "OK",
                data={"exists": 10, "uidvalidity": 7, "uidnext": 11, "read_only": False},
            ),
            ImapResponse("NO", text="mailbox does not exist"),
        ]

        selected = await session.select("INBOX")
        self.assertEqual(selected.native_key, "INBOX")
        self.assertEqual(selected.exists, 10)
        self.assertEqual(session.state, ImapSessionState.SELECTED)
        self.assertEqual(session.selected_mailbox, selected)

        with self.assertRaises(ProviderError) as captured:
            await session.select("Missing")
        self.assertEqual(captured.exception.code, ProviderErrorCode.MAILBOX_NOT_FOUND)
        self.assertEqual(session.state, ImapSessionState.SELECTED)
        self.assertEqual(session.selected_mailbox, selected)

    async def test_disconnect_is_idempotent(self):
        session, transport = await self.connect_session()
        await session.disconnect()
        await session.disconnect()
        self.assertEqual(session.state, ImapSessionState.DISCONNECTED)
        self.assertEqual(transport.close_count, 1)

    async def test_credentials_proxy_and_transport_do_not_enter_repr_or_logs(self):
        secret = "mail-password-never-log"
        proxy_secret = "proxy-password-never-log"
        transport = FakeImapTransport()
        credentials = ImapCredentials("user@example.com", secret, auth_kind="password")
        session = ImapSession(ProviderRegistry.default().get("gmail"), transport)

        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        root = logging.getLogger()
        root.addHandler(handler)
        try:
            await session.connect(
                credentials,
                self.endpoint,
                proxy=f"http://proxy:{proxy_secret}@proxy.example:8080",
            )
            await session.execute(ImapCommand.identity("NOOP", timeout_seconds=1))
        finally:
            root.removeHandler(handler)

        rendered = f"{credentials!r} {session!r} {stream.getvalue()}"
        self.assertNotIn(secret, rendered)
        self.assertNotIn(proxy_secret, rendered)
        self.assertNotIn("proxy-user", rendered)

    async def test_capability_discovery_filters_but_never_invents_features(self):
        gmail, gmail_transport = await self.connect_session(provider="gmail")
        self.assertIn("IDLE", gmail.server_capabilities)
        self.assertIn("IDLE", gmail.capabilities)
        self.assertNotIn("MOVE", gmail.capabilities)
        self.assertNotIn("CONDSTORE", gmail.capabilities)
        self.assertNotIn("QRESYNC", gmail.capabilities)

        no_idle_transport = FakeImapTransport()
        no_idle_transport.responses["CAPABILITY"] = [
            ImapResponse("OK", data=[b"* CAPABILITY IMAP4rev1 UIDPLUS"])
        ]
        gmail_without_idle, _ = await self.connect_session(
            provider="gmail",
            transport=no_idle_transport,
        )
        self.assertNotIn("IDLE", gmail_without_idle.capabilities)

        generic, _ = await self.connect_session(provider="generic")
        self.assertIn("IDLE", generic.server_capabilities)
        self.assertNotIn("IDLE", generic.capabilities)
        self.assertEqual(gmail_transport.close_count, 0)

    async def test_closing_idle_iterator_closes_source_before_next_command(self):
        session, transport = await self.connect_session()
        transport.responses["SELECT"] = [ImapResponse("OK", data={"exists": 1})]
        await session.select("INBOX")
        source_closed = asyncio.Event()
        never = asyncio.Event()

        async def idle_events() -> AsyncIterator[IdleEvent]:
            try:
                yield IdleEvent("exists", count=2)
                await never.wait()
            finally:
                source_closed.set()

        transport.idle_source = idle_events()
        iterator = session.idle()
        event = await anext(iterator)
        self.assertEqual(event.kind, "exists")
        self.assertEqual(session.state, ImapSessionState.IDLING)
        await iterator.aclose()

        self.assertTrue(source_closed.is_set())
        self.assertEqual(session.state, ImapSessionState.SELECTED)
        await session.execute(ImapCommand.identity("NOOP", timeout_seconds=1))

    async def test_idle_owns_session_until_iterator_is_closed(self):
        session, transport = await self.connect_session()
        transport.responses["SELECT"] = [ImapResponse("OK", data={"exists": 1})]
        await session.select("INBOX")
        release_idle = asyncio.Event()
        yielded = asyncio.Event()

        async def idle_events() -> AsyncIterator[IdleEvent]:
            yield IdleEvent("exists", count=2)
            await release_idle.wait()

        transport.idle_source = idle_events()

        async def consume_idle() -> None:
            async for event in session.idle():
                self.assertEqual(event.kind, "exists")
                yielded.set()
                await release_idle.wait()

        idle_task = asyncio.create_task(consume_idle())
        await asyncio.wait_for(yielded.wait(), timeout=1)
        noop_task = asyncio.create_task(
            session.execute(ImapCommand.identity("NOOP", timeout_seconds=1))
        )
        await asyncio.sleep(0.02)
        self.assertNotIn(("start", "NOOP"), transport.records)
        self.assertEqual(session.state, ImapSessionState.IDLING)

        release_idle.set()
        await asyncio.wait_for(idle_task, timeout=1)
        await asyncio.wait_for(noop_task, timeout=1)
        self.assertEqual(session.state, ImapSessionState.SELECTED)
        self.assertIn(("start", "NOOP"), transport.records)


class AccountConnectionLimiterTests(unittest.IsolatedAsyncioTestCase):
    async def test_third_normal_connection_is_rejected_until_permit_released(self):
        limiter = AccountConnectionLimiter(
            ProviderRegistry.default(),
            normal_connections_per_account=2,
            idle_connections_per_account=1,
        )
        first = await limiter.acquire("acc_one", "gmail", kind="normal")
        second = await limiter.acquire("acc_one", "gmail", kind="normal")
        with self.assertRaises(ProviderError) as captured:
            await limiter.acquire("acc_one", "gmail", kind="normal")
        self.assertEqual(captured.exception.code, ProviderErrorCode.RATE_LIMITED)

        await first.release()
        replacement = await limiter.acquire("acc_one", "gmail", kind="normal")
        await replacement.release()
        await second.release()
        await second.release()
        self.assertEqual(limiter.snapshot("acc_one", "gmail").normal_active, 0)

    async def test_idle_and_normal_limits_are_tracked_separately(self):
        limiter = AccountConnectionLimiter(ProviderRegistry.default())
        idle = await limiter.acquire("acc_one", "gmail", kind="idle")
        first = await limiter.acquire("acc_one", "gmail", kind="normal")
        second = await limiter.acquire("acc_one", "gmail", kind="normal")
        with self.assertRaises(ProviderError):
            await limiter.acquire("acc_one", "gmail", kind="idle")
        self.assertEqual(limiter.snapshot("acc_one", "gmail").provider_active, 3)
        await idle.release()
        await first.release()
        await second.release()

    async def test_provider_rate_limit_reduces_permits_and_recovers_one_at_a_time(self):
        now = [100.0]
        limiter = AccountConnectionLimiter(
            ProviderRegistry.default(),
            now_fn=lambda: now[0],
        )
        self.assertEqual(limiter.provider_limit("gmail"), 3)
        ignored = await limiter.record_error(
            "gmail",
            ProviderError(ProviderErrorCode.AUTHENTICATION_FAILED),
            cooldown_seconds=30,
        )
        self.assertFalse(ignored)
        self.assertEqual(limiter.provider_limit("gmail"), 3)
        reduced = await limiter.record_error(
            "gmail",
            ProviderError(ProviderErrorCode.RATE_LIMITED),
            cooldown_seconds=30,
        )
        self.assertTrue(reduced)
        self.assertEqual(limiter.provider_limit("gmail"), 2)
        self.assertEqual(limiter.provider_cooldown_until("gmail"), 130.0)

        await limiter.record_success("gmail")
        self.assertEqual(limiter.provider_limit("gmail"), 2)
        now[0] = 131.0
        await limiter.record_success("gmail")
        self.assertEqual(limiter.provider_limit("gmail"), 3)

    async def test_provider_limit_is_shared_across_accounts(self):
        limiter = AccountConnectionLimiter(ProviderRegistry.default())
        permits = [
            await limiter.acquire(f"acc_{index}", "netease", kind="normal")
            for index in range(2)
        ]
        with self.assertRaises(ProviderError) as captured:
            await limiter.acquire("acc_third", "netease", kind="normal")
        self.assertEqual(captured.exception.code, ProviderErrorCode.RATE_LIMITED)
        for permit in permits:
            await permit.release()


if __name__ == "__main__":
    unittest.main()
