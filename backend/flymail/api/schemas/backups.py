"""Administrator backup archive and restore-rehearsal schemas."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class BackupArchiveResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    status: str
    archive_name: str
    size_bytes: int = Field(ge=0)
    archive_sha256: str | None
    app_version: str | None
    schema_version: int | None
    created_at: float
    updated_at: float
    completed_at: float | None
    last_error_class: str
    last_error_message: str


class BackupArchiveListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[BackupArchiveResponse, ...]


class BackupInspectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    backup_id: str
    valid: bool
    compatible: bool
    format_version: int
    app_version: str
    schema_version: int
    archive_sha256: str
    file_count: int = Field(ge=0)
    total_uncompressed_bytes: int = Field(ge=0)
    warnings: tuple[str, ...] = ()


class RestoreRehearsalResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    backup_id: str
    restored_schema_version: int
    restored_table_count: int = Field(ge=0)
    verified_file_count: int = Field(ge=0)
    temporary_database_removed: bool
    temporary_files_removed: bool
