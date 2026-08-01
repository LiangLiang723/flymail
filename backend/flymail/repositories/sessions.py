"""SQL-only server-side session persistence for FlyMail V2."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

import aiomysql

from flymail.domain.ids import new_id


@dataclass(frozen=True, slots=True)
class SessionRecord:
    id: str
    user_uid: str
    username: str
    role: str
    user_enabled: bool
    session_password_version: int
    user_password_version: int
    csrf_token_hash: str
    expires_at: float
    revoked_at: float | None
    last_seen_at: float
    created_at: float

    @property
    def is_current(self) -> bool:
        return self.session_password_version == self.user_password_version


def _required_text(value: str, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{label} is required")
    return normalized


def _timestamp(value: float | None) -> float:
    timestamp = float(time.time() if value is None else value)
    if not math.isfinite(timestamp) or timestamp < 0:
        raise ValueError("timestamp must be finite and non-negative")
    return timestamp


def _map_session(row) -> SessionRecord:
    return SessionRecord(
        id=str(row["id"]),
        user_uid=str(row["user_uid"]),
        username=str(row["username"]),
        role=str(row["role"]),
        user_enabled=bool(row["user_enabled"]),
        session_password_version=int(row["session_password_version"] or 0),
        user_password_version=int(row["user_password_version"] or 0),
        csrf_token_hash=str(row["csrf_token_hash"] or ""),
        expires_at=float(row["expires_at"] or 0),
        revoked_at=(
            float(row["revoked_at"])
            if row["revoked_at"] is not None
            else None
        ),
        last_seen_at=float(row["last_seen_at"] or 0),
        created_at=float(row["created_at"] or 0),
    )


class SessionRepository:
    """Persist sessions on the caller-owned transaction."""

    def __init__(self, connection: aiomysql.Connection) -> None:
        self.connection = connection

    async def create(
        self,
        *,
        user_uid: str,
        token_hash: str,
        csrf_token_hash: str,
        password_version: int,
        expires_at: float,
        now: float | None = None,
    ) -> str:
        normalized_user = _required_text(user_uid, "user_uid")
        normalized_token_hash = _required_text(token_hash, "token_hash")
        normalized_csrf_hash = _required_text(csrf_token_hash, "csrf_token_hash")
        version = int(password_version)
        if version < 1:
            raise ValueError("password_version must be positive")
        created_at = _timestamp(now)
        expiry = _timestamp(expires_at)
        if expiry <= created_at:
            raise ValueError("session expiry must be in the future")
        session_id = new_id("ses")
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO user_sessions (
                    id, user_uid, token_hash, password_version,
                    csrf_token_hash, expires_at, revoked_at,
                    last_seen_at, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, NULL, %s, %s)
                """,
                (
                    session_id,
                    normalized_user,
                    normalized_token_hash,
                    version,
                    normalized_csrf_hash,
                    expiry,
                    created_at,
                    created_at,
                ),
            )
        return session_id

    async def get_for_authentication(
        self,
        session_id: str,
        token_hash: str,
        *,
        now: float | None = None,
        for_update: bool = False,
    ) -> SessionRecord | None:
        normalized_id = _required_text(session_id, "session_id")
        normalized_hash = _required_text(token_hash, "token_hash")
        timestamp = _timestamp(now)
        suffix = " FOR UPDATE" if for_update else ""
        async with self.connection.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                f"""
                SELECT s.id, s.user_uid, u.username, u.role,
                       u.enabled AS user_enabled,
                       s.password_version AS session_password_version,
                       u.password_version AS user_password_version,
                       s.csrf_token_hash, s.expires_at, s.revoked_at,
                       s.last_seen_at, s.created_at
                FROM user_sessions s
                JOIN users u ON u.id = s.user_uid
                WHERE s.id = %s AND s.token_hash = %s
                  AND s.revoked_at IS NULL AND s.expires_at > %s
                {suffix}
                """,
                (normalized_id, normalized_hash, timestamp),
            )
            row = await cursor.fetchone()
        return _map_session(row) if row else None

    async def touch(self, session_id: str, *, now: float | None = None) -> bool:
        timestamp = _timestamp(now)
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                """
                UPDATE user_sessions
                SET last_seen_at = %s
                WHERE id = %s AND revoked_at IS NULL AND expires_at > %s
                """,
                (_timestamp(now), _required_text(session_id, "session_id"), timestamp),
            )
            return cursor.rowcount == 1

    async def revoke(
        self,
        session_id: str,
        *,
        now: float | None = None,
    ) -> bool:
        timestamp = _timestamp(now)
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                """
                UPDATE user_sessions
                SET revoked_at = COALESCE(revoked_at, %s)
                WHERE id = %s
                """,
                (timestamp, _required_text(session_id, "session_id")),
            )
            return cursor.rowcount == 1

    async def revoke_user_sessions(
        self,
        user_uid: str,
        *,
        except_session_id: str | None = None,
        now: float | None = None,
    ) -> int:
        timestamp = _timestamp(now)
        normalized_user = _required_text(user_uid, "user_uid")
        normalized_exception = str(except_session_id or "").strip()
        sql = """
            UPDATE user_sessions
            SET revoked_at = %s
            WHERE user_uid = %s AND revoked_at IS NULL
        """
        params: list[object] = [timestamp, normalized_user]
        if normalized_exception:
            sql += " AND id <> %s"
            params.append(normalized_exception)
        async with self.connection.cursor() as cursor:
            await cursor.execute(sql, tuple(params))
            return int(cursor.rowcount)

    async def set_password_version_for_active_sessions(
        self,
        user_uid: str,
        password_version: int,
        *,
        only_session_id: str | None = None,
    ) -> int:
        normalized_user = _required_text(user_uid, "user_uid")
        version = int(password_version)
        if version < 1:
            raise ValueError("password_version must be positive")
        sql = """
            UPDATE user_sessions
            SET password_version = %s
            WHERE user_uid = %s AND revoked_at IS NULL
        """
        params: list[object] = [version, normalized_user]
        normalized_session = str(only_session_id or "").strip()
        if normalized_session:
            sql += " AND id = %s"
            params.append(normalized_session)
        async with self.connection.cursor() as cursor:
            await cursor.execute(sql, tuple(params))
            return int(cursor.rowcount)
