"""Isolated FlyMail V2 API development entrypoint.

This module deliberately does not import the legacy application or routes.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from flymail.config import FlyMailSettings
from flymail.infrastructure.db.migrations.runner import current_schema_version
from flymail.infrastructure.db.pool import DatabasePool
from flymail.infrastructure.object_store.store import ObjectStore
from version import VERSION


EXPECTED_SCHEMA_VERSION = 5


def _probe_object_store(settings: FlyMailSettings) -> None:
    ObjectStore(settings.object_dir, settings.object_tmp_dir)
    probe_path = settings.object_tmp_dir / f".health-{uuid.uuid4().hex}"
    descriptor: int | None = None
    try:
        descriptor = os.open(probe_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.write(descriptor, b"ok")
        os.fsync(descriptor)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        probe_path.unlink(missing_ok=True)


async def inspect_foundation_health(settings: FlyMailSettings) -> dict[str, str | int]:
    schema_version = 0
    database_status = "error"
    object_store_status = "error"
    pool: DatabasePool | None = None
    try:
        pool = await DatabasePool.create(settings)
        async with pool.acquire() as connection:
            schema_version = await current_schema_version(connection)
        database_status = "ok"
    except Exception:
        database_status = "error"
    finally:
        if pool is not None:
            await pool.close()

    try:
        await asyncio.to_thread(_probe_object_store, settings)
        object_store_status = "ok"
    except Exception:
        object_store_status = "error"

    status = (
        "ok"
        if database_status == "ok"
        and object_store_status == "ok"
        and schema_version == EXPECTED_SCHEMA_VERSION
        else "error"
    )
    return {
        "status": status,
        "role": settings.role,
        "schema_version": schema_version,
        "database": database_status,
        "object_store": object_store_status,
    }


def create_app(settings: FlyMailSettings | None = None) -> FastAPI:
    runtime_settings = settings or FlyMailSettings.from_env("api")
    app = FastAPI(
        title="FlyMail V2",
        description="FlyMail V2 isolated development API",
        version=VERSION,
    )

    @app.get("/api/v2/health")
    async def health() -> JSONResponse:
        payload = await inspect_foundation_health(runtime_settings)
        return JSONResponse(
            status_code=200 if payload["status"] == "ok" else 503,
            content=payload,
        )

    return app


def main() -> None:
    settings = FlyMailSettings.from_env("api")
    uvicorn.run(
        create_app(settings),
        host=os.environ.get("APP_HOST", "127.0.0.1"),
        port=int(os.environ.get("APP_PORT", "8081")),
        log_level="warning",
    )


if __name__ == "__main__":
    main()
