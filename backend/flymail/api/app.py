"""FlyMail V2 FastAPI application factory and health boundary."""

from __future__ import annotations

import asyncio
import inspect
import os
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from flymail.api.errors import (
    api_contract_error_handler,
    authentication_error_handler,
    authorization_error_handler,
    conflict_error_handler,
    csrf_error_handler,
    http_error_handler,
    invalid_credentials_error_handler,
    not_found_error_handler,
    rate_limit_error_handler,
    unsafe_endpoint_error_handler,
    unexpected_error_handler,
    unsupported_provider_error_handler,
    validation_error_handler,
)
from flymail.api.middleware import RequestContextMiddleware
from flymail.api.routes.accounts import router as accounts_router
from flymail.api.routes.admin import router as admin_router
from flymail.api.routes.admin_sync import router as admin_sync_router
from flymail.api.routes.auth import router as auth_router
from flymail.api.routes.backups import router as backups_router
from flymail.api.routes.bootstrap import router as bootstrap_router
from flymail.api.routes.compose import router as compose_router
from flymail.api.routes.contacts import router as contacts_router
from flymail.api.routes.content import router as content_router
from flymail.api.routes.notifications import router as notifications_router
from flymail.api.routes.operations import router as operations_router
from flymail.api.routes.profiles import router as profiles_router
from flymail.api.routes.realtime import router as realtime_router
from flymail.api.routes.search import router as search_router
from flymail.api.routes.settings import router as settings_router
from flymail.api.routes.storage import router as storage_router
from flymail.api.routes.sync import router as sync_router
from flymail.api.routes.threads import router as threads_router
from flymail.api.schemas.common import HealthResponse, VersionResponse
from flymail.application.accounts import AccountsService
from flymail.application.auth import AuthService
from flymail.application.backups import BackupService
from flymail.application.bootstrap import BootstrapService
from flymail.application.compose import ComposeService
from flymail.application.content import ContentApiService
from flymail.application.notification_config import NotificationConfigService
from flymail.application.notifications_api import NotificationApiService
from flymail.application.operations import MailOperationApiService
from flymail.application.personal import PersonalService
from flymail.application.realtime import RealtimeService
from flymail.application.search_queries import SearchQueryService
from flymail.application.settings_contacts import (
    AdminHistorySyncService,
    SettingsContactsService,
)
from flymail.application.storage_paths import StoragePathService
from flymail.application.sync_status import SyncStatusService
from flymail.application.thread_queries import ThreadQueryService
from flymail.config import FlyMailSettings
from flymail.domain.errors import (
    ApiContractError,
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    CsrfError,
    InvalidCredentialsError,
    NotFoundError,
    RateLimitError,
    UnsafeEndpointError,
    UnsupportedProviderError,
)
from flymail.domain.ids import new_id
from flymail.infrastructure.db.migrations.runner import (
    LATEST_SCHEMA_VERSION,
    current_schema_version,
    run_migrations,
)
from flymail.infrastructure.db.pool import DatabasePool
from flymail.infrastructure.object_store.store import ObjectStore
from flymail.infrastructure.security.credentials import CredentialCipher
from flymail.repositories.runtime import RuntimeRepository
from version import VERSION


_WORKER_STALE_MULTIPLIER = 3
_STARTUP_GRACE_MULTIPLIER = 6


def _probe_object_store(settings: FlyMailSettings) -> None:
    ObjectStore(settings.object_dir, settings.object_tmp_dir)
    probe_path = settings.object_tmp_dir / f".health-{uuid.uuid4().hex}"
    descriptor: int | None = None
    try:
        descriptor = os.open(
            probe_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        os.write(descriptor, b"ok")
        os.fsync(descriptor)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        probe_path.unlink(missing_ok=True)


async def _close_realtime_manager(app: FastAPI) -> None:
    manager = getattr(app.state, "realtime_manager", None)
    close = getattr(manager, "close", None)
    if close is None:
        return
    result = close()
    if inspect.isawaitable(result):
        await result


async def inspect_health(request: Request) -> tuple[HealthResponse, int]:
    app = request.app
    settings: FlyMailSettings = app.state.settings
    now = float(app.state.now_fn())
    schema_version = 0
    database_status = "error"
    schema_status = "error"
    worker_status = "unknown"
    worker_heartbeat_at: float | None = None

    db_started = time.perf_counter()
    pool: DatabasePool | None = getattr(app.state, "database_pool", None)
    if pool is not None and not pool.closed:
        try:
            async with pool.acquire() as connection:
                async with connection.cursor() as cursor:
                    await cursor.execute("SELECT 1")
                    await cursor.fetchone()
                schema_version = await current_schema_version(connection)
                worker_heartbeat_at = await RuntimeRepository(
                    connection
                ).latest_heartbeat("worker")
            database_status = "ok"
            schema_status = (
                "ok"
                if schema_version == LATEST_SCHEMA_VERSION
                else "outdated"
            )
        except Exception:
            database_status = "error"
            schema_status = "error"
    request.state.db_time_ms = float(
        getattr(request.state, "db_time_ms", 0.0)
    ) + max((time.perf_counter() - db_started) * 1000, 0.0)

    object_store_status = "error"
    object_started = time.perf_counter()
    try:
        await asyncio.to_thread(_probe_object_store, settings)
        object_store_status = "ok"
    except Exception:
        object_store_status = "error"
    request.state.object_time_ms = float(
        getattr(request.state, "object_time_ms", 0.0)
    ) + max((time.perf_counter() - object_started) * 1000, 0.0)

    if database_status == "ok":
        if worker_heartbeat_at is None:
            worker_status = "missing"
        else:
            stale_after = max(
                float(settings.worker_heartbeat_seconds)
                * _WORKER_STALE_MULTIPLIER,
                30.0,
            )
            age = max(now - worker_heartbeat_at, 0.0)
            worker_status = "ok" if age <= stale_after else "stale"

    infrastructure_ok = (
        database_status == "ok"
        and schema_status == "ok"
        and object_store_status == "ok"
    )
    if not infrastructure_ok:
        overall_status, status_code = "error", 503
    elif worker_status == "ok":
        overall_status, status_code = "ok", 200
    else:
        startup_grace = max(
            float(settings.job_lease_seconds),
            float(settings.worker_heartbeat_seconds)
            * _STARTUP_GRACE_MULTIPLIER,
        )
        uptime = max(now - float(app.state.started_at), 0.0)
        if uptime <= startup_grace:
            overall_status, status_code = "degraded", 200
        else:
            overall_status, status_code = "error", 503

    return (
        HealthResponse(
            status=overall_status,
            version=VERSION,
            database=database_status,
            schema_status=schema_status,
            schema_version=schema_version,
            expected_schema_version=LATEST_SCHEMA_VERSION,
            worker=worker_status,
            worker_heartbeat_at=worker_heartbeat_at,
            object_store=object_store_status,
        ),
        status_code,
    )


def create_app(settings: FlyMailSettings) -> FastAPI:
    if settings.role != "api":
        raise ValueError("V2 API requires api settings")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        pool: DatabasePool | None = None
        app.state.accepting_requests = False
        try:
            pool = await DatabasePool.create(settings)
            app.state.database_pool = pool
            await run_migrations(pool)
            store = ObjectStore(settings.object_dir, settings.object_tmp_dir)
            await asyncio.to_thread(_probe_object_store, settings)
            app.state.object_store = store
            app.state.auth_service = AuthService(
                pool,
                settings.session_secret,
                now_fn=app.state.now_fn,
            )
            app.state.accounts_service = AccountsService(
                pool,
                settings.session_secret,
            )
            app.state.bootstrap_service = BootstrapService(pool)
            app.state.thread_query_service = ThreadQueryService(
                pool,
                store,
                settings.session_secret,
                now_fn=app.state.now_fn,
            )
            app.state.mail_operation_api_service = MailOperationApiService(
                pool,
                settings.session_secret,
                now_fn=app.state.now_fn,
            )
            app.state.content_api_service = ContentApiService(
                pool,
                store,
                now_fn=app.state.now_fn,
            )
            app.state.search_query_service = SearchQueryService(
                pool,
                settings.session_secret,
                now_fn=app.state.now_fn,
            )
            app.state.compose_service = ComposeService(
                pool,
                store,
                now_fn=app.state.now_fn,
            )
            app.state.realtime_service = RealtimeService(
                pool,
                auth_service=app.state.auth_service,
                now_fn=app.state.now_fn,
            )
            app.state.settings_contacts_service = SettingsContactsService(
                pool,
                app.state.realtime_service,
                now_fn=app.state.now_fn,
            )
            app.state.admin_history_sync_service = AdminHistorySyncService(
                pool,
                app.state.realtime_service,
                now_fn=app.state.now_fn,
            )
            app.state.sync_status_service = SyncStatusService(
                pool,
                app.state.realtime_service,
                now_fn=app.state.now_fn,
            )
            app.state.personal_service = PersonalService(
                pool,
                store,
                app.state.realtime_service,
                now_fn=app.state.now_fn,
            )
            app.state.notification_api_service = NotificationApiService(
                pool,
                app.state.realtime_service,
                settings.session_secret,
                now_fn=app.state.now_fn,
            )
            app.state.notification_config_service = NotificationConfigService(
                pool,
                CredentialCipher.from_master_secret(settings.session_secret),
                now_fn=app.state.now_fn,
            )
            app.state.storage_path_service = StoragePathService(
                pool,
                settings.data_dir,
                now_fn=app.state.now_fn,
            )
            app.state.backup_service = BackupService(
                pool,
                settings,
                now_fn=app.state.now_fn,
            )
            app.state.api_process_id = new_id("api")
            async with pool.acquire() as connection:
                await connection.begin()
                try:
                    await RuntimeRepository(connection).touch_process(
                        app.state.api_process_id,
                        "api",
                        now=float(app.state.now_fn()),
                    )
                    await connection.commit()
                except Exception:
                    await connection.rollback()
                    raise
            app.state.started_at = float(app.state.now_fn())
            app.state.accepting_requests = True
            yield
        finally:
            app.state.accepting_requests = False
            try:
                await _close_realtime_manager(app)
            finally:
                if pool is not None:
                    await pool.close()

    app = FastAPI(
        title="FlyMail V2",
        description="FlyMail V2 isolated development API",
        version=VERSION,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.now_fn = time.time
    app.state.started_at = float(app.state.now_fn())
    app.state.realtime_manager = None
    app.state.database_pool = None
    app.state.object_store = None
    app.state.auth_service = None
    app.state.accounts_service = None
    app.state.bootstrap_service = None
    app.state.thread_query_service = None
    app.state.mail_operation_api_service = None
    app.state.content_api_service = None
    app.state.search_query_service = None
    app.state.compose_service = None
    app.state.realtime_service = None
    app.state.settings_contacts_service = None
    app.state.admin_history_sync_service = None
    app.state.sync_status_service = None
    app.state.personal_service = None
    app.state.notification_api_service = None
    app.state.notification_config_service = None
    app.state.storage_path_service = None
    app.state.backup_service = None
    app.state.accepting_requests = False

    app.add_middleware(RequestContextMiddleware)
    app.add_exception_handler(ApiContractError, api_contract_error_handler)
    app.add_exception_handler(InvalidCredentialsError, invalid_credentials_error_handler)
    app.add_exception_handler(AuthenticationError, authentication_error_handler)
    app.add_exception_handler(CsrfError, csrf_error_handler)
    app.add_exception_handler(RateLimitError, rate_limit_error_handler)
    app.add_exception_handler(UnsafeEndpointError, unsafe_endpoint_error_handler)
    app.add_exception_handler(
        UnsupportedProviderError,
        unsupported_provider_error_handler,
    )
    app.add_exception_handler(AuthorizationError, authorization_error_handler)
    app.add_exception_handler(ConflictError, conflict_error_handler)
    app.add_exception_handler(NotFoundError, not_found_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_error_handler)
    app.add_exception_handler(Exception, unexpected_error_handler)

    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(accounts_router)
    app.include_router(bootstrap_router)
    app.include_router(threads_router)
    app.include_router(operations_router)
    app.include_router(content_router)
    app.include_router(search_router)
    app.include_router(compose_router)
    app.include_router(realtime_router)
    app.include_router(settings_router)
    app.include_router(contacts_router)
    app.include_router(admin_sync_router)
    app.include_router(sync_router)
    app.include_router(profiles_router)
    app.include_router(notifications_router)
    app.include_router(storage_router)
    app.include_router(backups_router)

    @app.get("/api/health", response_model=HealthResponse)
    @app.get("/api/v2/health", response_model=HealthResponse)
    async def health(request: Request) -> JSONResponse:
        payload, status_code = await inspect_health(request)
        return JSONResponse(
            status_code=status_code,
            content=payload.model_dump(mode="json", by_alias=True),
        )

    @app.get("/api/v2/version", response_model=VersionResponse)
    async def version() -> VersionResponse:
        return VersionResponse(
            version=VERSION,
            schema_version=LATEST_SCHEMA_VERSION,
        )

    return app
