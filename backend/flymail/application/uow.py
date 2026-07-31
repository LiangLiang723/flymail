"""Application-facing Unit of Work protocol."""

from __future__ import annotations

from types import TracebackType
from typing import Protocol, Self, runtime_checkable

import aiomysql


@runtime_checkable
class ApplicationUnitOfWork(Protocol):
    connection: aiomysql.Connection | None

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...
