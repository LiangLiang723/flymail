"""Lightweight IMAP IDLE supervision for FlyMail V2."""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from flymail.providers.core.imap_commands import IdleEvent
from flymail.providers.core.rate_limit import AccountConnectionLimiter
from flymail.providers.errors import ProviderError, ProviderErrorCode


@dataclass(frozen=True, slots=True)
class IdleAccountSnapshot:
    account_id: str
    user_uid: str
    provider_key: str
    mailbox_id: str
    mailbox_native_key: str
    credential_version: int
    status: str
    supports_idle: bool
    idle_refresh_seconds: float
    poll_seconds: float

    def __post_init__(self) -> None:
        for field_name in (
            "account_id",
            "user_uid",
            "provider_key",
            "mailbox_id",
            "mailbox_native_key",
            "status",
        ):
            value = str(getattr(self, field_name) or "").strip()
            if not value:
                raise ValueError(f"{field_name} is required")
            if field_name in {"provider_key", "status"}:
                value = value.casefold()
            object.__setattr__(self, field_name, value)
        if self.status not in {"active", "disabled", "auth_required", "deleting"}:
            raise ValueError("unsupported IDLE account status")
        if isinstance(self.credential_version, bool) or int(self.credential_version) < 1:
            raise ValueError("credential_version must be at least 1")
        object.__setattr__(self, "credential_version", int(self.credential_version))
        if not isinstance(self.supports_idle, bool):
            raise TypeError("supports_idle must be bool")
        for field_name in ("idle_refresh_seconds", "poll_seconds"):
            value = float(getattr(self, field_name))
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{field_name} must be finite and positive")
            object.__setattr__(self, field_name, value)


class IdleAccountSource(Protocol):
    async def load(self, account_id: str) -> IdleAccountSnapshot | None: ...

    async def is_current(self, snapshot: IdleAccountSnapshot) -> bool: ...


class IdleSession(Protocol):
    def idle(self, mailbox_native_key: str) -> AsyncIterator[IdleEvent]: ...

    async def disconnect(self) -> None: ...


class IdleSessionFactory(Protocol):
    async def open(self, snapshot: IdleAccountSnapshot) -> IdleSession: ...


class IdlePublisher(Protocol):
    async def publish_incremental(
        self,
        account: IdleAccountSnapshot,
        *,
        reason: str,
        now: float | None = None,
    ) -> str: ...

    async def publish_reconcile(
        self,
        account: IdleAccountSnapshot,
        *,
        reason: str,
        now: float | None = None,
    ) -> str: ...

    async def publish_mailbox_refresh(
        self,
        account: IdleAccountSnapshot,
        *,
        reason: str,
        now: float | None = None,
    ) -> str: ...


class IdleSupervisor:
    """Own one account's IDLE connection and publish only lightweight work.

    MIME parsing, summary ingestion, and message writes are deliberately absent
    from this module. Each protocol event becomes a deduplicated durable job.
    """

    _INCREMENTAL_REASONS = {
        "exists": "message_exists",
        "recent": "message_exists",
        "expunge": "message_expunge",
        "fetch": "flags_changed",
    }

    def __init__(
        self,
        account_source: IdleAccountSource,
        session_factory: IdleSessionFactory,
        publisher: IdlePublisher,
        limiter: AccountConnectionLimiter,
        *,
        stop_event: asyncio.Event | None = None,
        reconnect_delay_seconds: float = 5,
        state_check_seconds: float = 5,
        now_fn: Callable[[], float] = time.time,
        sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        delay = float(reconnect_delay_seconds)
        if not math.isfinite(delay) or delay < 0:
            raise ValueError("reconnect_delay_seconds must be finite and non-negative")
        state_check = float(state_check_seconds)
        if not math.isfinite(state_check) or state_check <= 0:
            raise ValueError("state_check_seconds must be finite and positive")
        if not isinstance(limiter, AccountConnectionLimiter):
            raise TypeError("limiter must be AccountConnectionLimiter")
        self.account_source = account_source
        self.session_factory = session_factory
        self.publisher = publisher
        self.limiter = limiter
        self.stop_event = stop_event or asyncio.Event()
        self.reconnect_delay_seconds = delay
        self.state_check_seconds = state_check
        self.now_fn = now_fn
        self.sleep_fn = sleep_fn

    async def run_account(self, account_id: str) -> None:
        normalized_account = str(account_id or "").strip()
        if not normalized_account:
            raise ValueError("account_id is required")
        disconnected = False
        while not self.stop_event.is_set():
            snapshot = await self.account_source.load(normalized_account)
            if (
                snapshot is None
                or snapshot.status != "active"
                or not await self.account_source.is_current(snapshot)
            ):
                return
            if not snapshot.supports_idle:
                await self.publisher.publish_mailbox_refresh(
                    snapshot,
                    reason="idle_unsupported",
                    now=float(self.now_fn()),
                )
                await self._wait(snapshot.poll_seconds)
                continue

            session: IdleSession | None = None
            try:
                async with await self.limiter.acquire(
                    snapshot.account_id,
                    snapshot.provider_key,
                    kind="idle",
                ):
                    session = await self.session_factory.open(snapshot)
                    if disconnected:
                        await self.publisher.publish_reconcile(
                            snapshot,
                            reason="network_recovered",
                            now=float(self.now_fn()),
                        )
                        disconnected = False
                    if self.stop_event.is_set():
                        return
                    await self._run_cycle(snapshot, session)
                    if not await self.account_source.is_current(snapshot):
                        return
                    await self._wait(self.reconnect_delay_seconds)
            except asyncio.CancelledError:
                raise
            except ProviderError as exc:
                if exc.code in {
                    ProviderErrorCode.AUTHENTICATION_FAILED,
                    ProviderErrorCode.AUTHORIZATION_REQUIRED,
                }:
                    return
                disconnected = exc.code in {
                    ProviderErrorCode.CONNECTION_FAILED,
                    ProviderErrorCode.TEMPORARY_SERVER_ERROR,
                }
                await self._wait(self.reconnect_delay_seconds)
            except (ConnectionError, TimeoutError, OSError):
                disconnected = True
                await self._wait(self.reconnect_delay_seconds)
            except Exception:
                await self._wait(self.reconnect_delay_seconds)
            finally:
                if session is not None:
                    try:
                        await session.disconnect()
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        disconnected = True

    async def _run_cycle(
        self,
        snapshot: IdleAccountSnapshot,
        session: IdleSession,
    ) -> None:
        source = session.idle(snapshot.mailbox_native_key)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + snapshot.idle_refresh_seconds
        next_event: asyncio.Task[IdleEvent] | None = asyncio.create_task(anext(source))
        try:
            while not self.stop_event.is_set():
                if not await self.account_source.is_current(snapshot):
                    return
                remaining = deadline - loop.time()
                if remaining <= 0:
                    return
                done, _pending = await asyncio.wait(
                    (next_event,),
                    timeout=min(remaining, self.state_check_seconds),
                )
                if not done:
                    continue
                try:
                    event = next_event.result()
                except StopAsyncIteration:
                    return
                next_event = None
                if not isinstance(event, IdleEvent):
                    raise TypeError("IDLE source must yield IdleEvent")
                if not await self.account_source.is_current(snapshot):
                    return
                if event.kind in self._INCREMENTAL_REASONS:
                    await self.publisher.publish_incremental(
                        snapshot,
                        reason=self._INCREMENTAL_REASONS[event.kind],
                        now=float(self.now_fn()),
                    )
                elif event.kind == "bye":
                    raise ConnectionError("IMAP IDLE connection closed")
                elif event.kind == "timeout":
                    return
                next_event = asyncio.create_task(anext(source))
        finally:
            if next_event is not None and not next_event.done():
                next_event.cancel()
                await asyncio.gather(next_event, return_exceptions=True)
            closer = getattr(source, "aclose", None)
            if closer is not None:
                await closer()

    async def _wait(self, seconds: float) -> None:
        if self.stop_event.is_set() or seconds <= 0:
            return
        sleep_task = asyncio.create_task(self.sleep_fn(seconds))
        stop_task = asyncio.create_task(self.stop_event.wait())
        done, pending = await asyncio.wait(
            (sleep_task, stop_task),
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            if task is sleep_task:
                await task
