"""V2 mailbox, message, and thread schema."""

from flymail.infrastructure.db.migrations import Migration


ID = "VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin"
SHA256 = "CHAR(64) CHARACTER SET ascii COLLATE ascii_bin"


MIGRATION = Migration(
    version=2,
    name="mail_and_threads",
    statements=(
        f"""
        CREATE TABLE IF NOT EXISTS mailboxes (
            id {ID} PRIMARY KEY,
            user_uid {ID} NOT NULL,
            account_id {ID} NOT NULL,
            native_key VARCHAR(512) NOT NULL,
            native_name VARCHAR(512) NOT NULL,
            semantic_key VARCHAR(64) NOT NULL DEFAULT 'custom',
            mailbox_type VARCHAR(32) NOT NULL DEFAULT 'folder',
            delimiter_value VARCHAR(16) NOT NULL DEFAULT '',
            attributes_json JSON NULL,
            uidvalidity BIGINT UNSIGNED NOT NULL DEFAULT 0,
            highest_modseq BIGINT UNSIGNED NOT NULL DEFAULT 0,
            total_count BIGINT NOT NULL DEFAULT 0,
            unread_count BIGINT NOT NULL DEFAULT 0,
            sync_status VARCHAR(32) NOT NULL DEFAULT 'pending',
            created_at DOUBLE NOT NULL DEFAULT 0,
            updated_at DOUBLE NOT NULL DEFAULT 0,
            UNIQUE KEY uq_mailboxes_account_native (account_id, native_key),
            KEY idx_mailboxes_user_semantic (user_uid, semantic_key, account_id, id),
            KEY idx_mailboxes_account_status (account_id, sync_status, id),
            CONSTRAINT chk_mailboxes_type CHECK (mailbox_type IN ('folder', 'label')),
            CONSTRAINT chk_mailboxes_counts CHECK (total_count >= 0 AND unread_count >= 0)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
        f"""
        CREATE TABLE IF NOT EXISTS threads (
            id {ID} PRIMARY KEY,
            user_uid {ID} NOT NULL,
            canonical_thread_key VARCHAR(191) NOT NULL,
            normalized_subject VARCHAR(512) NOT NULL DEFAULT '',
            created_at DOUBLE NOT NULL DEFAULT 0,
            updated_at DOUBLE NOT NULL DEFAULT 0,
            UNIQUE KEY uq_threads_user_key (user_uid, canonical_thread_key),
            KEY idx_threads_user_updated (user_uid, updated_at DESC, id DESC)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
        f"""
        CREATE TABLE IF NOT EXISTS messages (
            id {ID} PRIMARY KEY,
            user_uid {ID} NOT NULL,
            canonical_message_key VARCHAR(191) NOT NULL,
            message_id_header VARCHAR(998) NOT NULL DEFAULT '',
            thread_id {ID} NULL,
            subject TEXT NULL,
            normalized_subject VARCHAR(512) NOT NULL DEFAULT '',
            from_json JSON NULL,
            to_json JSON NULL,
            cc_json JSON NULL,
            bcc_json JSON NULL,
            reply_to_json JSON NULL,
            sent_at DOUBLE NOT NULL DEFAULT 0,
            received_at DOUBLE NOT NULL DEFAULT 0,
            size_bytes BIGINT NOT NULL DEFAULT 0,
            has_attachments TINYINT NOT NULL DEFAULT 0,
            snippet TEXT NULL,
            body_state VARCHAR(32) NOT NULL DEFAULT 'not_requested',
            search_state VARCHAR(32) NOT NULL DEFAULT 'metadata',
            created_at DOUBLE NOT NULL DEFAULT 0,
            updated_at DOUBLE NOT NULL DEFAULT 0,
            UNIQUE KEY uq_messages_user_key (user_uid, canonical_message_key),
            KEY idx_messages_user_thread_time (user_uid, thread_id, received_at DESC, id DESC),
            KEY idx_messages_user_received (user_uid, received_at DESC, id DESC),
            KEY idx_messages_message_id_header (user_uid, message_id_header(191)),
            CONSTRAINT chk_messages_size CHECK (size_bytes >= 0),
            CONSTRAINT chk_messages_body_state CHECK (body_state IN ('not_requested', 'queued', 'fetching', 'ready', 'evicted', 'failed', 'unavailable')),
            CONSTRAINT chk_messages_search_state CHECK (search_state IN ('metadata', 'queued', 'ready', 'evicted', 'failed'))
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
        f"""
        CREATE TABLE IF NOT EXISTS message_headers (
            message_id {ID} PRIMARY KEY,
            user_uid {ID} NOT NULL,
            in_reply_to VARCHAR(998) NOT NULL DEFAULT '',
            references_json JSON NULL,
            list_id VARCHAR(998) NOT NULL DEFAULT '',
            raw_header_object_sha256 {SHA256} NULL,
            parser_version INT NOT NULL DEFAULT 1,
            parsed_at DOUBLE NOT NULL DEFAULT 0,
            KEY idx_message_headers_user_reply (user_uid, in_reply_to(191)),
            KEY idx_message_headers_raw_object (raw_header_object_sha256)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
        f"""
        CREATE TABLE IF NOT EXISTS message_remote_instances (
            id {ID} PRIMARY KEY,
            user_uid {ID} NOT NULL,
            account_id {ID} NOT NULL,
            mailbox_id {ID} NOT NULL,
            message_id {ID} NOT NULL,
            uidvalidity BIGINT UNSIGNED NOT NULL,
            remote_uid BIGINT UNSIGNED NOT NULL,
            provider_message_id VARCHAR(191) NOT NULL DEFAULT '',
            provider_thread_id VARCHAR(191) NOT NULL DEFAULT '',
            flags_json JSON NULL,
            is_read TINYINT NOT NULL DEFAULT 0,
            is_starred TINYINT NOT NULL DEFAULT 0,
            remote_version VARCHAR(191) NOT NULL DEFAULT '',
            remote_deleted TINYINT NOT NULL DEFAULT 0,
            last_seen_at DOUBLE NOT NULL DEFAULT 0,
            created_at DOUBLE NOT NULL DEFAULT 0,
            updated_at DOUBLE NOT NULL DEFAULT 0,
            UNIQUE KEY uq_remote_identity (account_id, mailbox_id, uidvalidity, remote_uid),
            KEY idx_remote_instances_message (user_uid, message_id, account_id, id),
            KEY idx_remote_instances_provider_id (account_id, provider_message_id),
            KEY idx_remote_instances_mailbox_state (account_id, mailbox_id, remote_deleted, remote_uid)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
        f"""
        CREATE TABLE IF NOT EXISTS message_memberships (
            remote_instance_id {ID} NOT NULL,
            mailbox_id {ID} NOT NULL,
            user_uid {ID} NOT NULL,
            membership_kind VARCHAR(32) NOT NULL DEFAULT 'folder',
            provider_label VARCHAR(512) NOT NULL DEFAULT '',
            created_at DOUBLE NOT NULL DEFAULT 0,
            updated_at DOUBLE NOT NULL DEFAULT 0,
            PRIMARY KEY (remote_instance_id, mailbox_id),
            KEY idx_memberships_mailbox_instance (mailbox_id, remote_instance_id),
            KEY idx_memberships_user_mailbox (user_uid, mailbox_id, remote_instance_id),
            CONSTRAINT chk_memberships_kind CHECK (membership_kind IN ('folder', 'label'))
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
        f"""
        CREATE TABLE IF NOT EXISTS thread_messages (
            thread_id {ID} NOT NULL,
            message_id {ID} NOT NULL,
            user_uid {ID} NOT NULL,
            parent_message_id {ID} NULL,
            relation_source VARCHAR(32) NOT NULL DEFAULT 'headers',
            position_hint BIGINT NOT NULL DEFAULT 0,
            created_at DOUBLE NOT NULL DEFAULT 0,
            PRIMARY KEY (thread_id, message_id),
            KEY idx_thread_messages_user_message (user_uid, message_id, thread_id),
            KEY idx_thread_messages_parent (thread_id, parent_message_id),
            CONSTRAINT chk_thread_messages_source CHECK (relation_source IN ('headers', 'fallback'))
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
        f"""
        CREATE TABLE IF NOT EXISTS thread_projections (
            user_uid {ID} NOT NULL,
            semantic_mailbox VARCHAR(64) NOT NULL,
            thread_id {ID} NOT NULL,
            latest_message_id {ID} NOT NULL,
            latest_message_at DOUBLE NOT NULL DEFAULT 0,
            subject TEXT NULL,
            participants_summary TEXT NULL,
            latest_snippet TEXT NULL,
            message_count BIGINT NOT NULL DEFAULT 0,
            unread_count BIGINT NOT NULL DEFAULT 0,
            is_starred TINYINT NOT NULL DEFAULT 0,
            has_attachments TINYINT NOT NULL DEFAULT 0,
            account_count INT NOT NULL DEFAULT 0,
            pending_operation_count BIGINT NOT NULL DEFAULT 0,
            projection_version BIGINT NOT NULL DEFAULT 1,
            updated_at DOUBLE NOT NULL DEFAULT 0,
            PRIMARY KEY (user_uid, semantic_mailbox, thread_id),
            KEY idx_thread_projection_cursor (user_uid, semantic_mailbox, latest_message_at DESC, thread_id DESC),
            KEY idx_thread_projection_unread (user_uid, semantic_mailbox, unread_count, latest_message_at DESC, thread_id DESC),
            KEY idx_thread_projection_starred (user_uid, semantic_mailbox, is_starred, latest_message_at DESC, thread_id DESC),
            CONSTRAINT chk_thread_projection_counts CHECK (message_count >= 0 AND unread_count >= 0 AND account_count >= 0 AND pending_operation_count >= 0)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
    ),
)
