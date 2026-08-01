"""Persist exact MIME body part metadata per remote message instance."""

from flymail.infrastructure.db.migrations import Migration


ID = "VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin"


MIGRATION = Migration(
    version=8,
    name="message_body_parts",
    statements=(
        f"""
        CREATE TABLE IF NOT EXISTS message_body_parts (
            id {ID} PRIMARY KEY,
            user_uid {ID} NOT NULL,
            message_id {ID} NOT NULL,
            remote_instance_id {ID} NOT NULL,
            body_kind VARCHAR(16) NOT NULL,
            imap_part VARCHAR(64) NOT NULL,
            content_type VARCHAR(255) NOT NULL,
            charset VARCHAR(64) NOT NULL DEFAULT '',
            transfer_encoding VARCHAR(64) NOT NULL DEFAULT '',
            remote_size_bytes BIGINT NOT NULL DEFAULT 0,
            created_at DOUBLE NOT NULL DEFAULT 0,
            updated_at DOUBLE NOT NULL DEFAULT 0,
            UNIQUE KEY uq_message_body_parts_kind (remote_instance_id, body_kind),
            KEY idx_message_body_parts_message (user_uid, message_id, remote_instance_id),
            CONSTRAINT chk_message_body_parts_kind CHECK (body_kind IN ('text', 'html')),
            CONSTRAINT chk_message_body_parts_size CHECK (remote_size_bytes >= 0)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
    ),
)
