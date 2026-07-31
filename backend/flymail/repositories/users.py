"""Typed FlyMail V2 user repository with explicit admin operations."""

from __future__ import annotations

import time
from dataclasses import dataclass

import aiomysql
import pymysql

from flymail.domain.errors import ConflictError
from flymail.domain.ids import new_id
from flymail.repositories.base import AdminContext, TenantContext, fetch_one
from flymail.repositories.settings import SettingsRepository


@dataclass(frozen=True, slots=True)
class User:
    id: str
    username: str
    role: str
    enabled: bool
    password_version: int
    created_at: float
    updated_at: float


def _map_user(row) -> User:
    return User(
        id=str(row["id"]),
        username=str(row["username"]),
        role=str(row["role"]),
        enabled=bool(row["enabled"]),
        password_version=int(row["password_version"] or 0),
        created_at=float(row["created_at"] or 0),
        updated_at=float(row["updated_at"] or 0),
    )


class UserRepository:
    def __init__(self, connection: aiomysql.Connection) -> None:
        self.connection = connection

    async def get_user(self, tenant: TenantContext) -> User | None:
        row = await fetch_one(
            self.connection,
            """
            SELECT id, username, role, enabled, password_version,
                   created_at, updated_at
            FROM users
            WHERE id = %s
            """,
            (tenant.user_uid,),
        )
        return _map_user(row) if row else None

    async def get_user_for_admin(
        self,
        admin: AdminContext,
        user_uid: str,
    ) -> User | None:
        del admin
        row = await fetch_one(
            self.connection,
            """
            SELECT id, username, role, enabled, password_version,
                   created_at, updated_at
            FROM users
            WHERE id = %s
            """,
            (str(user_uid or "").strip(),),
        )
        return _map_user(row) if row else None

    async def create_user_for_admin(
        self,
        admin: AdminContext,
        *,
        username: str,
        password_hash: str,
        role: str = "user",
        enabled: bool = True,
    ) -> User:
        del admin
        normalized_username = str(username or "").strip()
        if not normalized_username:
            raise ValueError("username is required")
        if not str(password_hash or ""):
            raise ValueError("password_hash is required")
        if role not in {"admin", "user"}:
            raise ValueError("unsupported user role")

        user_uid = new_id("usr")
        now = time.time()
        try:
            async with self.connection.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO users (
                        id, username, password_hash, role, enabled,
                        password_version, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, 1, %s, %s)
                    """,
                    (
                        user_uid,
                        normalized_username,
                        password_hash,
                        role,
                        1 if enabled else 0,
                        now,
                        now,
                    ),
                )
                await cursor.execute(
                    """
                    INSERT INTO user_profiles (
                        user_uid, nickname, avatar_object_sha256, created_at, updated_at
                    ) VALUES (%s, '', NULL, %s, %s)
                    """,
                    (user_uid, now, now),
                )
            await SettingsRepository(self.connection).create_defaults(
                TenantContext(user_uid),
                now=now,
            )
        except pymysql.err.IntegrityError as exc:
            if int(exc.args[0] or 0) == 1062:
                raise ConflictError("user already exists") from None
            raise

        return User(
            id=user_uid,
            username=normalized_username,
            role=role,
            enabled=bool(enabled),
            password_version=1,
            created_at=now,
            updated_at=now,
        )

    async def set_enabled_for_admin(
        self,
        admin: AdminContext,
        user_uid: str,
        enabled: bool,
    ) -> bool:
        del admin
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                """
                UPDATE users
                SET enabled = %s, updated_at = %s
                WHERE id = %s
                """,
                (1 if enabled else 0, time.time(), str(user_uid or "").strip()),
            )
            return cursor.rowcount > 0
