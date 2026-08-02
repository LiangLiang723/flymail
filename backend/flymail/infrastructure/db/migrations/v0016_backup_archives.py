"""Persist administrator-created backup archive metadata."""

from flymail.infrastructure.db.migrations import Migration


ID = "VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin"
SHA256 = "CHAR(64) CHARACTER SET ascii COLLATE ascii_bin"


MIGRATION = Migration(
    version=16,
    name="backup_archives",
    statements=(
        f"""
        CREATE TABLE IF NOT EXISTS backup_archives (
            id {ID} PRIMARY KEY,
            created_by {ID} NOT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'creating',
            archive_name VARCHAR(255) NOT NULL,
            archive_sha256 {SHA256} NULL,
            size_bytes BIGINT NOT NULL DEFAULT 0,
            manifest_json JSON NULL,
            last_error_class VARCHAR(96) NOT NULL DEFAULT '',
            last_error_message VARCHAR(512) NOT NULL DEFAULT '',
            created_at DOUBLE NOT NULL DEFAULT 0,
            updated_at DOUBLE NOT NULL DEFAULT 0,
            completed_at DOUBLE NULL,
            KEY idx_backup_archives_created (created_at DESC, id DESC),
            KEY idx_backup_archives_status (status, updated_at, id),
            CONSTRAINT chk_backup_archives_status CHECK (
                status IN ('creating', 'completed', 'failed')
            ),
            CONSTRAINT chk_backup_archives_size CHECK (size_bytes >= 0)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
    ),
)
