"""Local authentication, sessions, CSRF, rate limits, and admin security flows."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import Final

from cryptography.exceptions import InvalidSignature

from flymail.domain.errors import (
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    CsrfError,
    InvalidCredentialsError,
    NotFoundError,
    RateLimitError,
)
from flymail.infrastructure.db.pool import DatabasePool
from flymail.infrastructure.security.passwords import hash_password, verify_password
from flymail.infrastructure.security.sessions import (
    new_session_token,
    sign_session_cookie,
    verify_session_cookie,
)
from flymail.repositories.audit import AuditRepository
from flymail.repositories.base import AdminContext
from flymail.repositories.rate_limits import LoginRateLimitRepository
from flymail.repositories.sessions import SessionRecord, SessionRepository
from flymail.repositories.users import AuthenticationUser, User, UserRepository


SESSION_COOKIE_NAME: Final = "flymail_v2_session"
_SESSION_TTL_SECONDS: Final = 7 * 24 * 3600
_RATE_WINDOW_SECONDS: Final = 5 * 60
_RATE_BLOCK_SECONDS: Final = 5 * 60
_RATE_MAX_FAILURES: Final = 5


@dataclass(frozen=True, slots=True)
class LoginResult:
    user: User
    session_id: str
    cookie_value: str
    csrf_token: str
    expires_at: float


@dataclass(frozen=True, slots=True)
class AuthenticatedSession:
    record: SessionRecord
    user: User
    csrf_token: str

    @property
    def session_id(self) -> str:
        return self.record.id


class _LocalRateLimiter:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._failures: dict[tuple[str, str], tuple[float, int]] = {}
        self._blocked_until: dict[tuple[str, str], float] = {}

    async def assert_allowed(self, key: tuple[str, str], now: float) -> None:
        async with self._lock:
            blocked_until = float(self._blocked_until.get(key, 0.0))
            if blocked_until > now:
                raise RateLimitError("login rate limit exceeded")
            if blocked_until:
                self._blocked_until.pop(key, None)
                self._failures.pop(key, None)

    async def record_failure(self, key: tuple[str, str], now: float) -> bool:
        async with self._lock:
            started, count = self._failures.get(key, (now, 0))
            if now - started >= _RATE_WINDOW_SECONDS:
                started, count = now, 0
            count += 1
            self._failures[key] = (started, count)
            if count >= _RATE_MAX_FAILURES:
                self._blocked_until[key] = now + _RATE_BLOCK_SECONDS
                return True
            return False

    async def clear(self, key: tuple[str, str]) -> None:
        async with self._lock:
            self._failures.pop(key, None)
            self._blocked_until.pop(key, None)


class AuthService:
    """Coordinate security mutations with explicit transactional boundaries."""

    def __init__(
        self,
        pool: DatabasePool,
        session_secret: str,
        *,
        now_fn=time.time,
    ) -> None:
        if not isinstance(pool, DatabasePool):
            raise TypeError("pool must be DatabasePool")
        normalized_secret = str(session_secret or "")
        if len(normalized_secret) < 16:
            raise ValueError("session_secret must be at least 16 characters")
        self.pool = pool
        self.secret = normalized_secret.encode("utf-8")
        self.now_fn = now_fn
        self.local_limiter = _LocalRateLimiter()
        self._dummy_password_hash = hash_password("flymail-invalid-login-dummy")

    @staticmethod
    def _normalize_username(username: str) -> str:
        return str(username or "").strip()

    @staticmethod
    def _validate_new_password(password: str) -> str:
        value = str(password or "")
        if value == "":
            raise ConflictError("new password cannot be empty")
        return value

    def _opaque_hash(self, label: str, value: str) -> str:
        return hmac.new(
            self.secret,
            f"flymail-v2/{label}/{value}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _rate_key(self, username: str, source: str) -> tuple[str, str]:
        principal = self._normalize_username(username).casefold()
        normalized_source = str(source or "unknown").strip().casefold() or "unknown"
        return (
            self._opaque_hash("login-principal", principal),
            self._opaque_hash("login-source", normalized_source),
        )

    def _csrf_token(self, raw_session_token: str) -> str:
        digest = hmac.new(
            self.secret,
            b"flymail-v2/csrf/v1/" + raw_session_token.encode("ascii"),
            hashlib.sha256,
        ).digest()
        return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    @staticmethod
    def _sha256(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _encode_cookie(self, session_id: str, raw_token: str) -> str:
        return sign_session_cookie(f"{session_id}:{raw_token}", self.secret)

    def _decode_cookie(self, cookie_value: str) -> tuple[str, str]:
        try:
            payload = verify_session_cookie(cookie_value, self.secret)
            session_id, raw_token = payload.split(":", 1)
            if not session_id or not raw_token:
                raise InvalidSignature
            return session_id, raw_token
        except (InvalidSignature, ValueError):
            raise AuthenticationError("invalid session") from None

    async def _persistent_rate_check(
        self,
        principal_hash: str,
        source_hash: str,
        now: float,
    ) -> None:
        blocked = False
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                window = await LoginRateLimitRepository(connection).lock_window(
                    principal_hash,
                    source_hash,
                    now=now,
                )
                blocked = window.blocked_until > now
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
        if blocked:
            raise RateLimitError("login rate limit exceeded")

    async def _record_login_failure(
        self,
        *,
        auth_user: AuthenticationUser | None,
        principal_hash: str,
        source_hash: str,
        request_id: str,
        now: float,
    ) -> bool:
        key = (principal_hash, source_hash)
        local_blocked = await self.local_limiter.record_failure(key, now)
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                repository = LoginRateLimitRepository(connection)
                window = await repository.lock_window(
                    principal_hash,
                    source_hash,
                    now=now,
                )
                if now - window.window_started_at >= _RATE_WINDOW_SECONDS:
                    window_started_at, count = now, 0
                else:
                    window_started_at, count = window.window_started_at, window.failure_count
                count += 1
                blocked = local_blocked or count >= _RATE_MAX_FAILURES
                blocked_until = now + _RATE_BLOCK_SECONDS if blocked else 0.0
                await repository.record_failure(
                    principal_hash,
                    source_hash,
                    failure_count=count,
                    window_started_at=window_started_at,
                    blocked_until=blocked_until,
                    now=now,
                )
                await AuditRepository(connection).append(
                    event_type="auth.login",
                    result_code="rate_limited" if blocked else "invalid_credentials",
                    request_id=request_id,
                    user_uid=auth_user.user.id if auth_user else None,
                    actor_user_uid=None,
                    resource_type="user",
                    resource_id=auth_user.user.id if auth_user else None,
                    safe_metadata={
                        "principal_hash": principal_hash,
                        "source_hash": source_hash,
                    },
                    now=now,
                )
                await connection.commit()
                return blocked
            except Exception:
                await connection.rollback()
                raise

    async def login(
        self,
        *,
        username: str,
        password: str,
        source: str,
        request_id: str,
    ) -> LoginResult:
        normalized_username = self._normalize_username(username)
        principal_hash, source_hash = self._rate_key(normalized_username, source)
        key = (principal_hash, source_hash)
        now = float(self.now_fn())
        await self.local_limiter.assert_allowed(key, now)
        await self._persistent_rate_check(principal_hash, source_hash, now)

        async with self.pool.acquire() as connection:
            auth_user = await UserRepository(connection).find_for_authentication(
                normalized_username
            )
        encoded = auth_user.password_hash if auth_user else self._dummy_password_hash
        password_valid = await asyncio.to_thread(verify_password, str(password or ""), encoded)
        if auth_user is None or not auth_user.user.enabled or not password_valid:
            blocked = await self._record_login_failure(
                auth_user=auth_user,
                principal_hash=principal_hash,
                source_hash=source_hash,
                request_id=request_id,
                now=now,
            )
            if blocked:
                raise RateLimitError("login rate limit exceeded")
            raise InvalidCredentialsError("invalid credentials")

        raw_token, token_hash = new_session_token()
        csrf_token = self._csrf_token(raw_token)
        csrf_hash = self._sha256(csrf_token)
        expires_at = now + _SESSION_TTL_SECONDS
        concurrent_change = False
        locked: AuthenticationUser | None = None
        session_id = ""
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                locked = await UserRepository(connection).find_for_authentication(
                    normalized_username,
                    for_update=True,
                )
                concurrent_change = (
                    locked is None
                    or not locked.user.enabled
                    or locked.password_hash != auth_user.password_hash
                )
                if concurrent_change:
                    await connection.rollback()
                else:
                    session_id = await SessionRepository(connection).create(
                        user_uid=locked.user.id,
                        token_hash=token_hash,
                        csrf_token_hash=csrf_hash,
                        password_version=locked.user.password_version,
                        expires_at=expires_at,
                        now=now,
                    )
                    await LoginRateLimitRepository(connection).clear(
                        principal_hash,
                        source_hash,
                    )
                    await AuditRepository(connection).append(
                        event_type="auth.login",
                        result_code="success",
                        request_id=request_id,
                        user_uid=locked.user.id,
                        actor_user_uid=locked.user.id,
                        resource_type="session",
                        resource_id=session_id,
                        safe_metadata={
                            "principal_hash": principal_hash,
                            "source_hash": source_hash,
                        },
                        now=now,
                    )
                    await connection.commit()
            except Exception:
                await connection.rollback()
                raise
        if concurrent_change or locked is None:
            blocked = await self._record_login_failure(
                auth_user=auth_user,
                principal_hash=principal_hash,
                source_hash=source_hash,
                request_id=request_id,
                now=now,
            )
            if blocked:
                raise RateLimitError("login rate limit exceeded")
            raise InvalidCredentialsError("invalid credentials")
        await self.local_limiter.clear(key)
        return LoginResult(
            user=locked.user,
            session_id=session_id,
            cookie_value=self._encode_cookie(session_id, raw_token),
            csrf_token=csrf_token,
            expires_at=expires_at,
        )

    async def authenticate(self, cookie_value: str) -> AuthenticatedSession:
        session_id, raw_token = self._decode_cookie(cookie_value)
        token_hash = self._sha256(raw_token)
        now = float(self.now_fn())
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                repository = SessionRepository(connection)
                record = await repository.get_for_authentication(
                    session_id,
                    token_hash,
                    now=now,
                    for_update=True,
                )
                if (
                    record is None
                    or not record.user_enabled
                    or not record.is_current
                ):
                    if record is not None:
                        await repository.revoke(session_id, now=now)
                    await connection.commit()
                    raise AuthenticationError("invalid session")
                csrf_token = self._csrf_token(raw_token)
                if not hmac.compare_digest(
                    record.csrf_token_hash,
                    self._sha256(csrf_token),
                ):
                    await repository.revoke(session_id, now=now)
                    await connection.commit()
                    raise AuthenticationError("invalid session")
                await repository.touch(session_id, now=now)
                await connection.commit()
            except AuthenticationError:
                raise
            except Exception:
                await connection.rollback()
                raise
        return AuthenticatedSession(
            record=record,
            user=User(
                id=record.user_uid,
                username=record.username,
                role=record.role,
                enabled=record.user_enabled,
                password_version=record.user_password_version,
                created_at=0,
                updated_at=0,
            ),
            csrf_token=csrf_token,
        )

    def validate_csrf(
        self,
        session: AuthenticatedSession,
        *,
        supplied_token: str,
        origin: str,
        expected_origin: str,
    ) -> None:
        if not origin or not hmac.compare_digest(origin, expected_origin):
            raise CsrfError("request origin is not allowed")
        if not supplied_token or not hmac.compare_digest(
            supplied_token,
            session.csrf_token,
        ):
            raise CsrfError("csrf token is invalid")

    async def logout(
        self,
        session: AuthenticatedSession,
        *,
        request_id: str,
    ) -> None:
        now = float(self.now_fn())
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                await SessionRepository(connection).revoke(session.session_id, now=now)
                await AuditRepository(connection).append(
                    event_type="auth.logout",
                    result_code="success",
                    request_id=request_id,
                    user_uid=session.user.id,
                    actor_user_uid=session.user.id,
                    resource_type="session",
                    resource_id=session.session_id,
                    now=now,
                )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise

    async def change_password(
        self,
        session: AuthenticatedSession,
        *,
        current_password: str,
        new_password: str,
        revoke_other_sessions: bool,
        request_id: str,
    ) -> User:
        new_value = self._validate_new_password(new_password)
        async with self.pool.acquire() as connection:
            current = await UserRepository(connection).get_for_authentication(
                session.user.id
            )
        if current is None or not await asyncio.to_thread(
            verify_password,
            str(current_password or ""),
            current.password_hash,
        ):
            raise InvalidCredentialsError("invalid credentials")
        new_hash = await asyncio.to_thread(hash_password, new_value)
        now = float(self.now_fn())
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                users = UserRepository(connection)
                locked = await users.get_for_authentication(
                    session.user.id,
                    for_update=True,
                )
                if locked is None or locked.password_hash != current.password_hash:
                    raise ConflictError("password changed concurrently")
                updated = await users.update_password(
                    session.user.id,
                    new_hash,
                    now=now,
                )
                if updated is None:
                    raise NotFoundError("user was not found")
                sessions = SessionRepository(connection)
                if revoke_other_sessions:
                    await sessions.set_password_version_for_active_sessions(
                        session.user.id,
                        updated.password_version,
                        only_session_id=session.session_id,
                    )
                    await sessions.revoke_user_sessions(
                        session.user.id,
                        except_session_id=session.session_id,
                        now=now,
                    )
                else:
                    await sessions.set_password_version_for_active_sessions(
                        session.user.id,
                        updated.password_version,
                    )
                await AuditRepository(connection).append(
                    event_type="auth.password_changed",
                    result_code="success",
                    request_id=request_id,
                    user_uid=session.user.id,
                    actor_user_uid=session.user.id,
                    resource_type="user",
                    resource_id=session.user.id,
                    safe_metadata={
                        "revoke_other_sessions": bool(revoke_other_sessions),
                    },
                    now=now,
                )
                await connection.commit()
                return updated
            except Exception:
                await connection.rollback()
                raise

    async def list_users(self, admin_user: User) -> tuple[User, ...]:
        self._require_admin(admin_user)
        async with self.pool.acquire() as connection:
            return await UserRepository(connection).list_users_for_admin(
                AdminContext(admin_user.id)
            )

    async def create_user(
        self,
        admin_user: User,
        *,
        username: str,
        password: str,
        role: str,
        enabled: bool,
        request_id: str,
    ) -> User:
        self._require_admin(admin_user)
        password_hash = await asyncio.to_thread(
            hash_password,
            self._validate_new_password(password),
        )
        now = float(self.now_fn())
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                created = await UserRepository(connection).create_user_for_admin(
                    AdminContext(admin_user.id),
                    username=username,
                    password_hash=password_hash,
                    role=role,
                    enabled=enabled,
                )
                await AuditRepository(connection).append(
                    event_type="admin.user_created",
                    result_code="success",
                    request_id=request_id,
                    user_uid=created.id,
                    actor_user_uid=admin_user.id,
                    resource_type="user",
                    resource_id=created.id,
                    safe_metadata={"role": role, "enabled": bool(enabled)},
                    now=now,
                )
                await connection.commit()
                return created
            except Exception:
                await connection.rollback()
                raise

    async def reset_password(
        self,
        admin_user: User,
        user_uid: str,
        *,
        new_password: str,
        request_id: str,
    ) -> User:
        self._require_admin(admin_user)
        password_hash = await asyncio.to_thread(
            hash_password,
            self._validate_new_password(new_password),
        )
        return await self._admin_password_update(
            admin_user,
            user_uid,
            password_hash=password_hash,
            event_type="admin.password_reset",
            request_id=request_id,
        )

    async def _admin_password_update(
        self,
        admin_user: User,
        user_uid: str,
        *,
        password_hash: str,
        event_type: str,
        request_id: str,
    ) -> User:
        now = float(self.now_fn())
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                updated = await UserRepository(connection).update_password(
                    user_uid,
                    password_hash,
                    now=now,
                )
                if updated is None:
                    raise NotFoundError("user was not found")
                await SessionRepository(connection).revoke_user_sessions(
                    user_uid,
                    now=now,
                )
                await AuditRepository(connection).append(
                    event_type=event_type,
                    result_code="success",
                    request_id=request_id,
                    user_uid=user_uid,
                    actor_user_uid=admin_user.id,
                    resource_type="user",
                    resource_id=user_uid,
                    now=now,
                )
                await connection.commit()
                return updated
            except Exception:
                await connection.rollback()
                raise

    async def set_user_enabled(
        self,
        admin_user: User,
        user_uid: str,
        *,
        enabled: bool,
        request_id: str,
    ) -> User:
        self._require_admin(admin_user)
        now = float(self.now_fn())
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                users = UserRepository(connection)
                changed = await users.set_enabled_for_admin(
                    AdminContext(admin_user.id),
                    user_uid,
                    enabled,
                )
                if not changed:
                    raise NotFoundError("user was not found")
                if not enabled:
                    await SessionRepository(connection).revoke_user_sessions(
                        user_uid,
                        now=now,
                    )
                updated = await users.get_user_for_admin(
                    AdminContext(admin_user.id),
                    user_uid,
                )
                if updated is None:
                    raise NotFoundError("user was not found")
                await AuditRepository(connection).append(
                    event_type=(
                        "admin.user_enabled" if enabled else "admin.user_disabled"
                    ),
                    result_code="success",
                    request_id=request_id,
                    user_uid=user_uid,
                    actor_user_uid=admin_user.id,
                    resource_type="user",
                    resource_id=user_uid,
                    now=now,
                )
                await connection.commit()
                return updated
            except Exception:
                await connection.rollback()
                raise

    async def revoke_user_sessions(
        self,
        admin_user: User,
        user_uid: str,
        *,
        request_id: str,
    ) -> int:
        self._require_admin(admin_user)
        now = float(self.now_fn())
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                target = await UserRepository(connection).get_user_for_admin(
                    AdminContext(admin_user.id),
                    user_uid,
                )
                if target is None:
                    raise NotFoundError("user was not found")
                revoked = await SessionRepository(connection).revoke_user_sessions(
                    user_uid,
                    now=now,
                )
                await AuditRepository(connection).append(
                    event_type="admin.sessions_revoked",
                    result_code="success",
                    request_id=request_id,
                    user_uid=user_uid,
                    actor_user_uid=admin_user.id,
                    resource_type="user",
                    resource_id=user_uid,
                    safe_metadata={"revoked_sessions": revoked},
                    now=now,
                )
                await connection.commit()
                return revoked
            except Exception:
                await connection.rollback()
                raise

    @staticmethod
    def _require_admin(user: User) -> None:
        if user.role != "admin" or not user.enabled:
            raise AuthorizationError("administrator role is required")
