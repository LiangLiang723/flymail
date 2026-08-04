"""Coordinate per-account mail synchronization work.

Interactive work should remain responsive, background work should not pile up,
and destructive or long-running maintenance must have exclusive access.
"""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import AsyncIterator


@dataclass
class _AccountSyncState:
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    interactive_count: int = 0
    background_active: bool = False
    exclusive_active: bool = False
    exclusive_waiters: int = 0


class AccountSyncCoordinator:
    def __init__(self) -> None:
        self._states: dict[str, _AccountSyncState] = {}
        self._states_lock = asyncio.Lock()

    async def _get_state(self, account_id: str) -> _AccountSyncState:
        async with self._states_lock:
            return self._states.setdefault(account_id, _AccountSyncState())

    @asynccontextmanager
    async def interactive(self, account_id: str) -> AsyncIterator[bool]:
        """Start user-initiated work unless an exclusive task owns the account."""
        state = await self._get_state(account_id)
        acquired = False
        async with state.condition:
            if not state.exclusive_active and state.exclusive_waiters == 0:
                state.interactive_count += 1
                acquired = True
        try:
            yield acquired
        finally:
            if acquired:
                async with state.condition:
                    state.interactive_count = max(0, state.interactive_count - 1)
                    state.condition.notify_all()

    @asynccontextmanager
    async def background(self, account_id: str) -> AsyncIterator[bool]:
        """Start one low-priority task if no higher-priority work is active."""
        state = await self._get_state(account_id)
        acquired = False
        async with state.condition:
            if (
                not state.exclusive_active
                and state.exclusive_waiters == 0
                and state.interactive_count == 0
                and not state.background_active
            ):
                state.background_active = True
                acquired = True
        try:
            yield acquired
        finally:
            if acquired:
                async with state.condition:
                    state.background_active = False
                    state.condition.notify_all()

    @asynccontextmanager
    async def exclusive(self, account_id: str) -> AsyncIterator[bool]:
        """Wait for current work and then exclusively own one account."""
        state = await self._get_state(account_id)
        async with state.condition:
            state.exclusive_waiters += 1
            try:
                await state.condition.wait_for(
                    lambda: (
                        not state.exclusive_active
                        and state.interactive_count == 0
                        and not state.background_active
                    )
                )
                state.exclusive_active = True
            finally:
                state.exclusive_waiters = max(0, state.exclusive_waiters - 1)
        try:
            yield True
        finally:
            async with state.condition:
                state.exclusive_active = False
                state.condition.notify_all()

    async def is_exclusive(self, account_id: str) -> bool:
        state = await self._get_state(account_id)
        async with state.condition:
            return state.exclusive_active or state.exclusive_waiters > 0

    async def should_yield_background(self, account_id: str) -> bool:
        state = await self._get_state(account_id)
        async with state.condition:
            return (
                state.exclusive_active
                or state.exclusive_waiters > 0
                or state.interactive_count > 0
            )

    async def remove(self, account_id: str) -> None:
        async with self._states_lock:
            state = self._states.get(account_id)
            if not state:
                return
            async with state.condition:
                if (
                    state.interactive_count == 0
                    and not state.background_active
                    and not state.exclusive_active
                    and state.exclusive_waiters == 0
                ):
                    self._states.pop(account_id, None)


sync_coordinator = AccountSyncCoordinator()
