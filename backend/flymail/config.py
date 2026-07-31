"""Explicit runtime settings for FlyMail V2 processes."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, cast

from flymail.domain.errors import ConfigurationError


ProcessRole = Literal["api", "worker"]


@dataclass(frozen=True, slots=True)
class FlyMailSettings:
    role: ProcessRole
    database_url: str = field(repr=False)
    data_dir: Path
    object_dir: Path
    object_tmp_dir: Path
    session_secret: str = field(repr=False)
    db_pool_name: str
    db_min_connections: int
    db_max_connections: int
    worker_heartbeat_seconds: int = 10
    job_lease_seconds: int = 60
    default_body_quota_bytes: int = 5 * 1024**3

    @classmethod
    def from_env(cls, role: ProcessRole) -> "FlyMailSettings":
        if role not in {"api", "worker"}:
            raise ConfigurationError(f"invalid process role: {role!r}")

        database_url = os.environ.get("DATABASE_URL", "").strip()
        if not database_url:
            raise ConfigurationError("DATABASE_URL is required")

        data_dir_value = os.environ.get("FLYMAIL_DATA_DIR", "").strip()
        if not data_dir_value:
            raise ConfigurationError("FLYMAIL_DATA_DIR is required")
        data_dir = Path(data_dir_value).expanduser()

        session_secret = os.environ.get("FLYMAIL_SESSION_SECRET", "").strip()
        if len(session_secret) < 16:
            raise ConfigurationError("FLYMAIL_SESSION_SECRET must be at least 16 characters")

        if role == "api":
            pool_name = "flymail-api"
            min_connections = 2
            max_connections = 12
        else:
            pool_name = "flymail-worker"
            min_connections = 2
            max_connections = 8

        return cls(
            role=cast(ProcessRole, role),
            database_url=database_url,
            data_dir=data_dir,
            object_dir=data_dir / "objects" / "sha256",
            object_tmp_dir=data_dir / "objects" / ".tmp",
            session_secret=session_secret,
            db_pool_name=pool_name,
            db_min_connections=min_connections,
            db_max_connections=max_connections,
        )
