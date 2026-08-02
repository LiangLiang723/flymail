"""Persist resumable query-scoped mail operations."""

from flymail.infrastructure.db.migrations import Migration


ID = "VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin"


MIGRATION = Migration(
    version=13,
    name="bulk_mail_operations",
    statements=(
        f"""
        CREATE TABLE IF NOT EXISTS bulk_mail_operations (
            id {ID} PRIMARY KEY,
            user_uid {ID} NOT NULL,
            operation_type VARCHAR(32) NOT NULL,
            filter_json JSON NOT NULL,
            cursor_remote_id {ID} NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'pending',
            matched_count BIGINT NOT NULL DEFAULT 0,
            operation_count BIGINT NOT NULL DEFAULT 0,
            idempotency_key VARCHAR(191) NOT NULL,
            last_error_class VARCHAR(96) NOT NULL DEFAULT '',
            last_error_message VARCHAR(512) NOT NULL DEFAULT '',
            created_at DOUBLE NOT NULL DEFAULT 0,
            updated_at DOUBLE NOT NULL DEFAULT 0,
            completed_at DOUBLE NULL,
            UNIQUE KEY uq_bulk_mail_operations_idempotency (user_uid, idempotency_key),
            KEY idx_bulk_mail_operations_user_status (user_uid, status, id),
            CONSTRAINT chk_bulk_mail_operations_type CHECK (operation_type IN ('set_read')),
            CONSTRAINT chk_bulk_mail_operations_status CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled')),
            CONSTRAINT chk_bulk_mail_operations_counts CHECK (matched_count >= 0 AND operation_count >= 0)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
    ),
)
