"""V2 content-addressed storage and local-search schema."""

from flymail.infrastructure.db.migrations import Migration


ID = "VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin"
SHA256 = "CHAR(64) CHARACTER SET ascii COLLATE ascii_bin"


def build_migration(*, use_ngram: bool) -> Migration:
    parser_clause = " WITH PARSER ngram" if use_ngram else ""
    return Migration(
        version=4,
        name="content_and_search",
        metadata={"fulltext_parser": "ngram" if use_ngram else "standard"},
        statements=(
            f"""
            CREATE TABLE IF NOT EXISTS content_objects (
                content_sha256 {SHA256} PRIMARY KEY,
                object_kind VARCHAR(32) NOT NULL,
                compression VARCHAR(16) NOT NULL DEFAULT 'none',
                original_size_bytes BIGINT NOT NULL DEFAULT 0,
                stored_size_bytes BIGINT NOT NULL DEFAULT 0,
                relative_path VARCHAR(255) NOT NULL,
                verified_at DOUBLE NULL,
                created_at DOUBLE NOT NULL DEFAULT 0,
                UNIQUE KEY uq_content_objects_path (relative_path),
                KEY idx_content_objects_kind_created (object_kind, created_at, content_sha256),
                CONSTRAINT chk_content_objects_kind CHECK (object_kind IN ('body_html', 'body_text', 'inline_image', 'attachment', 'raw_eml', 'draft_attachment', 'user_avatar', 'account_icon', 'contact_avatar', 'notification_asset')),
                CONSTRAINT chk_content_objects_compression CHECK (compression IN ('none', 'gzip', 'zstd')),
                CONSTRAINT chk_content_objects_sizes CHECK (original_size_bytes >= 0 AND stored_size_bytes >= 0)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
            """,
            f"""
            CREATE TABLE IF NOT EXISTS content_references (
                id {ID} PRIMARY KEY,
                user_uid {ID} NOT NULL,
                content_sha256 {SHA256} NOT NULL,
                reference_kind VARCHAR(32) NOT NULL,
                reference_id {ID} NOT NULL,
                pinned TINYINT NOT NULL DEFAULT 0,
                created_at DOUBLE NOT NULL DEFAULT 0,
                last_accessed_at DOUBLE NOT NULL DEFAULT 0,
                UNIQUE KEY uq_content_references_business (user_uid, content_sha256, reference_kind, reference_id),
                KEY idx_content_references_object (content_sha256, reference_kind, user_uid),
                KEY idx_content_references_user_lru (user_uid, reference_kind, pinned, last_accessed_at, id),
                CONSTRAINT chk_content_references_kind CHECK (reference_kind IN ('message_body_html', 'message_body_text', 'message_inline_image', 'message_attachment', 'raw_eml', 'draft_body_html', 'draft_body_text', 'draft_attachment', 'user_avatar', 'account_icon', 'contact_avatar', 'notification_asset'))
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
            """,
            f"""
            CREATE TABLE IF NOT EXISTS message_bodies (
                message_id {ID} PRIMARY KEY,
                user_uid {ID} NOT NULL,
                html_object_sha256 {SHA256} NULL,
                text_object_sha256 {SHA256} NULL,
                raw_eml_object_sha256 {SHA256} NULL,
                state VARCHAR(32) NOT NULL DEFAULT 'not_requested',
                body_size_bytes BIGINT NOT NULL DEFAULT 0,
                index_version INT NOT NULL DEFAULT 0,
                parser_version INT NOT NULL DEFAULT 1,
                checked_at DOUBLE NOT NULL DEFAULT 0,
                cached_at DOUBLE NULL,
                last_accessed_at DOUBLE NOT NULL DEFAULT 0,
                last_error_class VARCHAR(96) NOT NULL DEFAULT '',
                last_error_message VARCHAR(512) NOT NULL DEFAULT '',
                updated_at DOUBLE NOT NULL DEFAULT 0,
                KEY idx_message_bodies_user_state (user_uid, state, last_accessed_at, message_id),
                KEY idx_message_bodies_html_object (html_object_sha256),
                KEY idx_message_bodies_text_object (text_object_sha256),
                KEY idx_message_bodies_raw_object (raw_eml_object_sha256),
                CONSTRAINT chk_message_bodies_state CHECK (state IN ('not_requested', 'queued', 'fetching', 'ready', 'evicted', 'failed', 'unavailable')),
                CONSTRAINT chk_message_bodies_size CHECK (body_size_bytes >= 0)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
            """,
            f"""
            CREATE TABLE IF NOT EXISTS message_attachments (
                id {ID} PRIMARY KEY,
                user_uid {ID} NOT NULL,
                message_id {ID} NOT NULL,
                remote_instance_id {ID} NOT NULL,
                imap_part VARCHAR(64) NOT NULL,
                filename VARCHAR(1024) NOT NULL DEFAULT '',
                content_type VARCHAR(255) NOT NULL DEFAULT 'application/octet-stream',
                disposition VARCHAR(32) NOT NULL DEFAULT 'attachment',
                content_id VARCHAR(998) NOT NULL DEFAULT '',
                transfer_encoding VARCHAR(64) NOT NULL DEFAULT '',
                remote_size_bytes BIGINT NOT NULL DEFAULT 0,
                content_sha256 {SHA256} NULL,
                is_inline TINYINT NOT NULL DEFAULT 0,
                is_referenced_inline TINYINT NOT NULL DEFAULT 0,
                cache_state VARCHAR(32) NOT NULL DEFAULT 'not_requested',
                last_accessed_at DOUBLE NOT NULL DEFAULT 0,
                created_at DOUBLE NOT NULL DEFAULT 0,
                updated_at DOUBLE NOT NULL DEFAULT 0,
                UNIQUE KEY uq_message_attachments_part (remote_instance_id, imap_part),
                KEY idx_message_attachments_message (user_uid, message_id, is_inline, id),
                KEY idx_message_attachments_object (content_sha256, user_uid),
                KEY idx_message_attachments_lru (user_uid, is_inline, cache_state, last_accessed_at, id),
                CONSTRAINT chk_message_attachments_disposition CHECK (disposition IN ('attachment', 'inline', 'none')),
                CONSTRAINT chk_message_attachments_cache_state CHECK (cache_state IN ('not_requested', 'queued', 'fetching', 'ready', 'evicted', 'failed', 'unavailable')),
                CONSTRAINT chk_message_attachments_size CHECK (remote_size_bytes >= 0)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
            """,
            f"""
            CREATE TABLE IF NOT EXISTS body_search_documents (
                message_id {ID} PRIMARY KEY,
                user_uid {ID} NOT NULL,
                thread_id {ID} NULL,
                subject_text TEXT NULL,
                participants_text TEXT NULL,
                body_text LONGTEXT NULL,
                language VARCHAR(32) NOT NULL DEFAULT '',
                index_version INT NOT NULL DEFAULT 1,
                updated_at DOUBLE NOT NULL DEFAULT 0,
                KEY idx_body_search_user_thread (user_uid, thread_id, updated_at DESC, message_id),
                FULLTEXT KEY ft_body_search (subject_text, participants_text, body_text){parser_clause}
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
            """,
            f"""
            CREATE TABLE IF NOT EXISTS saved_searches (
                id {ID} PRIMARY KEY,
                user_uid {ID} NOT NULL,
                name VARCHAR(191) NOT NULL,
                filters_json JSON NOT NULL,
                is_pinned TINYINT NOT NULL DEFAULT 0,
                created_at DOUBLE NOT NULL DEFAULT 0,
                updated_at DOUBLE NOT NULL DEFAULT 0,
                UNIQUE KEY uq_saved_searches_user_name (user_uid, name),
                KEY idx_saved_searches_user_pinned (user_uid, is_pinned, updated_at DESC, id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
            """,
            f"""
            CREATE TABLE IF NOT EXISTS search_history (
                sequence_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                user_uid {ID} NOT NULL,
                filter_summary JSON NOT NULL,
                created_at DOUBLE NOT NULL DEFAULT 0,
                KEY idx_search_history_user_created (user_uid, created_at DESC, sequence_id DESC)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
            """,
            f"""
            CREATE TABLE IF NOT EXISTS backup_jobs (
                id {ID} PRIMARY KEY,
                user_uid {ID} NOT NULL,
                requested_by {ID} NOT NULL,
                status VARCHAR(32) NOT NULL DEFAULT 'pending',
                backup_scope VARCHAR(32) NOT NULL DEFAULT 'business',
                archive_path VARCHAR(1024) NOT NULL DEFAULT '',
                manifest_json JSON NULL,
                password_kdf_json JSON NULL,
                record_count BIGINT NOT NULL DEFAULT 0,
                object_count BIGINT NOT NULL DEFAULT 0,
                last_error_class VARCHAR(96) NOT NULL DEFAULT '',
                last_error_message VARCHAR(512) NOT NULL DEFAULT '',
                created_at DOUBLE NOT NULL DEFAULT 0,
                updated_at DOUBLE NOT NULL DEFAULT 0,
                finished_at DOUBLE NULL,
                KEY idx_backup_jobs_user_created (user_uid, created_at DESC, id DESC),
                KEY idx_backup_jobs_status (status, created_at, id),
                CONSTRAINT chk_backup_jobs_status CHECK (status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled', 'validating', 'review_required')),
                CONSTRAINT chk_backup_jobs_counts CHECK (record_count >= 0 AND object_count >= 0)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
            """,
        ),
    )
