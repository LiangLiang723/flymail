"""Explicit MySQL transaction boundary for FlyMail V2 application services."""

from __future__ import annotations

from types import TracebackType

import aiomysql

from flymail.infrastructure.db.pool import DatabasePool


class SqlUnitOfWork:
    """Own one acquired connection and one explicit transaction."""

    def __init__(self, pool: DatabasePool) -> None:
        self._pool = pool
        self._lease = None
        self.connection: aiomysql.Connection | None = None
        self._active = False
        self._completed = False

    async def __aenter__(self) -> "SqlUnitOfWork":
        if self._active:
            raise RuntimeError("unit of work is already active")
        self._lease = self._pool.acquire()
        connection = await self._lease.__aenter__()
        try:
            await connection.begin()
        except Exception:
            await self._lease.__aexit__(None, None, None)
            self._lease = None
            raise
        self.connection = connection
        self._active = True
        self._completed = False
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        lease = self._lease
        try:
            if self._active and not self._completed and self.connection is not None:
                await self.connection.rollback()
                self._completed = True
        finally:
            self.connection = None
            self._active = False
            self._lease = None
            if lease is not None:
                await lease.__aexit__(exc_type, exc, traceback)

    async def commit(self) -> None:
        connection = self._require_open_transaction()
        await connection.commit()
        self._completed = True

    async def rollback(self) -> None:
        connection = self._require_open_transaction()
        await connection.rollback()
        self._completed = True

    def _require_open_transaction(self) -> aiomysql.Connection:
        if not self._active or self.connection is None:
            raise RuntimeError("unit of work is not active")
        if self._completed:
            raise RuntimeError("unit of work transaction is already completed")
        return self.connection
