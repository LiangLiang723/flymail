"""Persist immutable draft snapshots for optimistic-conflict recovery."""

from flymail.infrastructure.db.migrations import Migration


ID = "VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin"
SHA256 = "CHAR(64) CHARACTER SET ascii COLLATE ascii_bin"


MIGRATION = Migration(
    version=14,
    name="draft_versions",
    statements=(
        f"""
        CREATE TABLE IF NOT EXISTS draft_versions (
            id {ID} PRIMARY KEY,
            draft_id {ID} NOT NULL,
            user_uid {ID} NOT NULL,
            version BIGINT NOT NULL,
            source VARCHAR(32) NOT NULL DEFAULT 'local',
            subject TEXT NULL,
            body_html_object_sha256 {SHA256} NULL,
            body_text_object_sha256 {SHA256} NULL,
            recipients_json JSON NOT NULL,
            created_at DOUBLE NOT NULL DEFAULT 0,
            KEY idx_draft_versions_draft (user_uid, draft_id, created_at DESC, id DESC),
            KEY idx_draft_versions_version (draft_id, version, source, id),
            CONSTRAINT chk_draft_versions_version CHECK (version >= 1),
            CONSTRAINT chk_draft_versions_source CHECK (source IN ('local', 'remote', 'conflict'))
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
    ),
)
