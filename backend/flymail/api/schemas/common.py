"""Shared response schemas for the FlyMail V2 API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ErrorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    request_id: str
    details: dict[str, Any] | None = None


class ErrorEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error: ErrorBody


class VersionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    schema_version: int


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    status: Literal["ok", "degraded", "error"]
    app: Literal["flymail"] = "flymail"
    role: Literal["api"] = "api"
    version: str
    api: Literal["ok"] = "ok"
    database: Literal["ok", "error"]
    schema_status: Literal["ok", "outdated", "error"] = Field(alias="schema")
    schema_version: int
    expected_schema_version: int
    worker: Literal["ok", "missing", "stale", "unknown"]
    worker_heartbeat_at: float | None = None
    object_store: Literal["ok", "error"]
