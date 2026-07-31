"""Role-isolated MySQL connection pools for FlyMail V2."""

from __future__ import annotations

from dataclasses import dataclass
from types import TracebackType
from typing import Any
from urllib.parse import quote, unquote, urlparse

import aiomysql

from flymail.config import FlyMailSettings
from flymail.domain.errors import ConfigurationError


_ALLOWED_MYSQL_SCHEMES = {"mysql", "mysql+aiomysql", "mysql+pymysql"}


@dataclass(frozen=True, slots=True)
class _DatabaseAddress:
    host: str
    port: int
    user: str
    password: str
    database: str


def _parse_database_url(url: str) -> _DatabaseAddress:
    value = str(url or "").strip()
    parsed = urlparse(value)
    if parsed.scheme.lower() not in _ALLOWED_MYSQL_SCHEMES:
        raise ConfigurationError("DATABASE_URL must use a MySQL scheme")
    database = unquote(parsed.path.lstrip("/"))
    if not database:
        raise ConfigurationError("DATABASE_URL must include a database name")
    user = unquote(parsed.username or "")
    if not user:
        raise ConfigurationError("DATABASE_URL must include a user")
    host = parsed.hostname or "127.0.0.1"
    return _DatabaseAddress(
        host=host,
        port=parsed.port or 3306,
        user=user,
        password=unquote(parsed.password or ""),
        database=database,
    )


def redacted_database_url(url: str) -> str:
    """Return a query-free MySQL URL that never contains the password."""

    address = _parse_database_url(url)
    host = address.host
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    safe_user = quote(address.user, safe="")
    safe_database = quote(address.database, safe="")
    return f"mysql://{safe_user}:***@{host}:{address.port}/{safe_database}"


class _ConnectionLease:
    def __init__(self, owner: "DatabasePool") -> None:
        self._owner = owner
        self._connection: aiomysql.Connection | None = None

    async def __aenter__(self) -> aiomysql.Connection:
        if self._owner.closed:
            raise RuntimeError(f"database pool {self._owner.name!r} is closed")
        connection = await self._owner._pool.acquire()
        self._connection = connection
        try:
            await connection.ping(reconnect=True)
        except Exception:
            self._owner._pool.release(connection)
            self._connection = None
            raise
        return connection

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        connection = self._connection
        self._connection = None
        if connection is None:
            return
        try:
            await connection.rollback()
        finally:
            self._owner._pool.release(connection)


class DatabasePool:
    """A named MySQL pool owned by one FlyMail process role."""

    def __init__(
        self,
        *,
        name: str,
        raw_pool: aiomysql.Pool,
        minsize: int,
        maxsize: int,
        safe_url: str,
    ) -> None:
        self.name = name
        self.minsize = minsize
        self.maxsize = maxsize
        self.safe_url = safe_url
        self._pool = raw_pool

    @classmethod
    async def create(cls, settings: FlyMailSettings) -> "DatabasePool":
        address = _parse_database_url(settings.database_url)
        connect_kwargs: dict[str, Any] = {
            "host": address.host,
            "port": address.port,
            "user": address.user,
            "password": address.password,
            "db": address.database,
            "charset": "utf8mb4",
            "autocommit": False,
            "minsize": settings.db_min_connections,
            "maxsize": settings.db_max_connections,
            "pool_recycle": 1800,
            "init_command": "SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED",
        }
        raw_pool = await aiomysql.create_pool(**connect_kwargs)
        return cls(
            name=settings.db_pool_name,
            raw_pool=raw_pool,
            minsize=settings.db_min_connections,
            maxsize=settings.db_max_connections,
            safe_url=redacted_database_url(settings.database_url),
        )

    @property
    def closed(self) -> bool:
        return bool(self._pool.closed)

    def acquire(self) -> _ConnectionLease:
        return _ConnectionLease(self)

    async def close(self) -> None:
        if self.closed:
            return
        self._pool.close()
        await self._pool.wait_closed()

    def __repr__(self) -> str:
        return (
            f"DatabasePool(name={self.name!r}, minsize={self.minsize}, "
            f"maxsize={self.maxsize}, url={self.safe_url!r}, closed={self.closed})"
        )
