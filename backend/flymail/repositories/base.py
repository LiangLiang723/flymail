"""Shared tenant contexts and typed MySQL fetch helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import aiomysql


def _required_identifier(value: str, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{label} is required")
    return normalized


@dataclass(frozen=True, slots=True)
class TenantContext:
    user_uid: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "user_uid", _required_identifier(self.user_uid, "user_uid"))


@dataclass(frozen=True, slots=True)
class AdminContext:
    actor_user_uid: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "actor_user_uid",
            _required_identifier(self.actor_user_uid, "actor_user_uid"),
        )


def normalize_email(value: str) -> str:
    normalized = str(value or "").strip().casefold()
    if not normalized or "@" not in normalized:
        raise ValueError("valid email address is required")
    return normalized


async def fetch_one(
    connection: aiomysql.Connection,
    sql: str,
    params: Sequence[Any] = (),
) -> Mapping[str, Any] | None:
    async with connection.cursor(aiomysql.DictCursor) as cursor:
        await cursor.execute(sql, params)
        row = await cursor.fetchone()
        return dict(row) if row else None


async def fetch_all(
    connection: aiomysql.Connection,
    sql: str,
    params: Sequence[Any] = (),
) -> list[Mapping[str, Any]]:
    async with connection.cursor(aiomysql.DictCursor) as cursor:
        await cursor.execute(sql, params)
        return [dict(row) for row in await cursor.fetchall()]
