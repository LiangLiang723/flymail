"""Adaptive account and provider connection permits for IMAP work."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Callable

from flymail.providers.errors import ProviderError, ProviderErrorCode
from flymail.providers.registry import ProviderRegistry


@dataclass(frozen=True, slots=True)
class ConnectionSnapshot:
    account_id: str
    provider_key: str
    normal_active: int
    idle_active: int
    provider_active: int
    provider_limit: int
    provider_cooldown_until: float


@dataclass(slots=True)
class _ProviderState:
    configured_limit: int
    dynamic_limit: int
    active: int = 0
    cooldown_until: float = 0.0


@dataclass(slots=True)
class _AccountState:
    normal_active: int = 0
    idle_active: int = 0


class ConnectionPermit:
    __slots__ = ("_limiter", "account_id", "provider_key", "kind", "_released")

    def __init__(
        self,
        limiter: "AccountConnectionLimiter",
        account_id: str,
        provider_key: str,
        kind: str,
    ) -> None:
        self._limiter = limiter
        self.account_id = account_id
        self.provider_key = provider_key
        self.kind = kind
        self._released = False

    async def release(self) -> None:
        if self._released:
            return
        self._released = True
        await self._limiter._release(self.account_id, self.provider_key, self.kind)

    async def __aenter__(self) -> "ConnectionPermit":
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        await self.release()


class AccountConnectionLimiter:
    """Reject excess connections and adapt provider-wide concurrency."""

    def __init__(
        self,
        registry: ProviderRegistry,
        *,
        normal_connections_per_account: int = 2,
        idle_connections_per_account: int = 1,
        recovery_interval_seconds: int = 60,
        now_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(registry, ProviderRegistry):
            raise TypeError("registry must be ProviderRegistry")
        for label, value in (
            ("normal_connections_per_account", normal_connections_per_account),
            ("idle_connections_per_account", idle_connections_per_account),
            ("recovery_interval_seconds", recovery_interval_seconds),
        ):
            if isinstance(value, bool) or int(value) < 1:
                raise ValueError(f"{label} must be at least 1")
        self._registry = registry
        self._normal_limit = int(normal_connections_per_account)
        self._idle_limit = int(idle_connections_per_account)
        self._recovery_interval = int(recovery_interval_seconds)
        self._now_fn = now_fn
        self._lock = asyncio.Lock()
        self._accounts: dict[tuple[str, str], _AccountState] = {}
        self._providers: dict[str, _ProviderState] = {
            key: _ProviderState(
                configured_limit=registry.get(key).capabilities().max_parallel_connections,
                dynamic_limit=registry.get(key).capabilities().max_parallel_connections,
            )
            for key in registry.keys()
        }

    async def acquire(
        self,
        account_id: str,
        provider_key: str,
        *,
        kind: str = "normal",
    ) -> ConnectionPermit:
        normalized_account = str(account_id or "").strip()
        normalized_provider = str(provider_key or "").strip().casefold()
        normalized_kind = str(kind or "").strip().casefold()
        if not normalized_account:
            raise ValueError("account_id is required")
        if normalized_kind not in {"normal", "idle"}:
            raise ValueError("connection kind must be normal or idle")
        self._registry.get(normalized_provider)

        async with self._lock:
            account_key = (normalized_account, normalized_provider)
            account_state = self._accounts.setdefault(account_key, _AccountState())
            provider_state = self._providers[normalized_provider]
            if normalized_kind == "normal" and account_state.normal_active >= self._normal_limit:
                raise self._limit_error(normalized_account, normalized_provider, normalized_kind)
            if normalized_kind == "idle" and account_state.idle_active >= self._idle_limit:
                raise self._limit_error(normalized_account, normalized_provider, normalized_kind)
            if provider_state.active >= provider_state.dynamic_limit:
                raise self._limit_error(normalized_account, normalized_provider, normalized_kind)

            if normalized_kind == "normal":
                account_state.normal_active += 1
            else:
                account_state.idle_active += 1
            provider_state.active += 1
            return ConnectionPermit(
                self,
                normalized_account,
                normalized_provider,
                normalized_kind,
            )

    async def record_error(
        self,
        provider_key: str,
        error: ProviderError,
        *,
        cooldown_seconds: int,
    ) -> bool:
        if not isinstance(error, ProviderError):
            raise TypeError("error must be ProviderError")
        if error.code is not ProviderErrorCode.RATE_LIMITED:
            return False
        await self.record_rate_limit(provider_key, cooldown_seconds=cooldown_seconds)
        return True

    async def record_rate_limit(self, provider_key: str, *, cooldown_seconds: int) -> None:
        normalized_provider = str(provider_key or "").strip().casefold()
        self._registry.get(normalized_provider)
        if isinstance(cooldown_seconds, bool) or int(cooldown_seconds) < 1:
            raise ValueError("cooldown_seconds must be at least 1")
        now = float(self._now_fn())
        async with self._lock:
            state = self._providers[normalized_provider]
            state.dynamic_limit = max(1, state.dynamic_limit - 1)
            state.cooldown_until = max(state.cooldown_until, now + int(cooldown_seconds))

    async def record_success(self, provider_key: str) -> None:
        normalized_provider = str(provider_key or "").strip().casefold()
        self._registry.get(normalized_provider)
        now = float(self._now_fn())
        async with self._lock:
            state = self._providers[normalized_provider]
            if now < state.cooldown_until:
                return
            if state.dynamic_limit < state.configured_limit:
                state.dynamic_limit += 1
                state.cooldown_until = (
                    0.0
                    if state.dynamic_limit >= state.configured_limit
                    else now + self._recovery_interval
                )

    def provider_limit(self, provider_key: str) -> int:
        normalized_provider = str(provider_key or "").strip().casefold()
        self._registry.get(normalized_provider)
        return self._providers[normalized_provider].dynamic_limit

    def provider_cooldown_until(self, provider_key: str) -> float:
        normalized_provider = str(provider_key or "").strip().casefold()
        self._registry.get(normalized_provider)
        return self._providers[normalized_provider].cooldown_until

    def snapshot(self, account_id: str, provider_key: str) -> ConnectionSnapshot:
        normalized_account = str(account_id or "").strip()
        normalized_provider = str(provider_key or "").strip().casefold()
        self._registry.get(normalized_provider)
        account = self._accounts.get((normalized_account, normalized_provider), _AccountState())
        provider = self._providers[normalized_provider]
        return ConnectionSnapshot(
            account_id=normalized_account,
            provider_key=normalized_provider,
            normal_active=account.normal_active,
            idle_active=account.idle_active,
            provider_active=provider.active,
            provider_limit=provider.dynamic_limit,
            provider_cooldown_until=provider.cooldown_until,
        )

    async def _release(self, account_id: str, provider_key: str, kind: str) -> None:
        async with self._lock:
            account_key = (account_id, provider_key)
            account = self._accounts.get(account_key)
            provider = self._providers[provider_key]
            if account is None:
                return
            if kind == "normal":
                account.normal_active = max(0, account.normal_active - 1)
            else:
                account.idle_active = max(0, account.idle_active - 1)
            provider.active = max(0, provider.active - 1)
            if account.normal_active == 0 and account.idle_active == 0:
                self._accounts.pop(account_key, None)

    def _limit_error(self, account_id: str, provider_key: str, kind: str) -> ProviderError:
        state = self._providers[provider_key]
        return ProviderError(
            ProviderErrorCode.RATE_LIMITED,
            debug_context={
                "account_id": account_id,
                "provider": provider_key,
                "connection_kind": kind,
                "provider_limit": state.dynamic_limit,
                "provider_active": state.active,
                "cooldown_until": state.cooldown_until,
            },
        )
