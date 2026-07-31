"""V2 operations, jobs, realtime, notifications, drafts, and sending schema."""

from flymail.infrastructure.db.migrations import Migration


ID = "VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin"
SHA256 = "CHAR(64) CHARACTER SET ascii COLLATE ascii_bin"


MIGRATION = Migration(
    version=3,
    name="jobs_realtime_and_drafts",
    statements=(
        f"""
        CREATE TABLE IF NOT EXISTS mail_operations (
            id {ID} PRIMARY KEY,
            user_uid {ID} NOT NULL,
            operation_group_id {ID} NULL,
            operation_type VARCHAR(64) NOT NULL,
            target_type VARCHAR(32) NOT NULL,
            target_id {ID} NOT NULL,
            account_id {ID} NULL,
            remote_instance_id {ID} NULL,
            desired_state JSON NULL,
            observed_remote_version VARCHAR(191) NOT NULL DEFAULT '',
            status VARCHAR(32) NOT NULL DEFAULT 'pending',
            priority INT NOT NULL DEFAULT 100,
            available_at DOUBLE NOT NULL DEFAULT 0,
            attempt_count INT NOT NULL DEFAULT 0,
            last_error_class VARCHAR(96) NOT NULL DEFAULT '',
            last_error_message VARCHAR(512) NOT NULL DEFAULT '',
            idempotency_key VARCHAR(191) NOT NULL,
            created_at DOUBLE NOT NULL DEFAULT 0,
            updated_at DOUBLE NOT NULL DEFAULT 0,
            completed_at DOUBLE NULL,
            UNIQUE KEY uq_mail_operation_idempotency (user_uid, idempotency_key),
            KEY idx_mail_operations_claim (status, available_at, priority, id),
            KEY idx_mail_operations_user_status (user_uid, status, created_at DESC, id DESC),
            KEY idx_mail_operations_target (user_uid, target_type, target_id, created_at DESC),
            CONSTRAINT chk_mail_operations_status CHECK (status IN ('pending', 'applying', 'synced', 'retry_wait', 'review_required', 'conflict', 'failed', 'cancelled')),
            CONSTRAINT chk_mail_operations_attempts CHECK (attempt_count >= 0)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
        f"""
        CREATE TABLE IF NOT EXISTS outbox_events (
            id {ID} PRIMARY KEY,
            user_uid {ID} NULL,
            aggregate_type VARCHAR(64) NOT NULL,
            aggregate_id {ID} NOT NULL,
            event_type VARCHAR(96) NOT NULL,
            payload JSON NOT NULL,
            created_at DOUBLE NOT NULL DEFAULT 0,
            published_at DOUBLE NULL,
            publish_attempts INT NOT NULL DEFAULT 0,
            last_error_class VARCHAR(96) NOT NULL DEFAULT '',
            last_error_message VARCHAR(512) NOT NULL DEFAULT '',
            KEY idx_outbox_unpublished (published_at, created_at, id),
            KEY idx_outbox_aggregate (aggregate_type, aggregate_id, created_at, id),
            CONSTRAINT chk_outbox_attempts CHECK (publish_attempts >= 0)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
        f"""
        CREATE TABLE IF NOT EXISTS worker_jobs (
            id {ID} PRIMARY KEY,
            user_uid {ID} NULL,
            queue_name VARCHAR(64) NOT NULL,
            job_kind VARCHAR(96) NOT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'pending',
            priority INT NOT NULL DEFAULT 100,
            available_at DOUBLE NOT NULL DEFAULT 0,
            lease_owner VARCHAR(191) NOT NULL DEFAULT '',
            lease_token {ID} NULL,
            lease_expires_at DOUBLE NULL,
            heartbeat_at DOUBLE NULL,
            attempt_count INT NOT NULL DEFAULT 0,
            max_attempts INT NOT NULL DEFAULT 10,
            dedupe_key VARCHAR(191) NULL,
            payload JSON NOT NULL,
            last_error_class VARCHAR(96) NOT NULL DEFAULT '',
            last_error_message VARCHAR(512) NOT NULL DEFAULT '',
            created_at DOUBLE NOT NULL DEFAULT 0,
            updated_at DOUBLE NOT NULL DEFAULT 0,
            finished_at DOUBLE NULL,
            UNIQUE KEY uq_worker_jobs_dedupe (queue_name, dedupe_key),
            KEY idx_worker_jobs_claim (queue_name, status, available_at, priority, id),
            KEY idx_worker_jobs_lease (status, lease_expires_at, id),
            KEY idx_worker_jobs_user_status (user_uid, status, created_at DESC, id DESC),
            CONSTRAINT chk_worker_jobs_status CHECK (status IN ('pending', 'leased', 'running', 'succeeded', 'retry_wait', 'failed', 'cancelled')),
            CONSTRAINT chk_worker_jobs_attempts CHECK (attempt_count >= 0 AND max_attempts >= 1)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
        f"""
        CREATE TABLE IF NOT EXISTS job_attempts (
            id {ID} PRIMARY KEY,
            job_id {ID} NOT NULL,
            attempt_number INT NOT NULL,
            worker_id VARCHAR(191) NOT NULL DEFAULT '',
            started_at DOUBLE NOT NULL DEFAULT 0,
            finished_at DOUBLE NULL,
            outcome VARCHAR(32) NOT NULL DEFAULT 'running',
            error_class VARCHAR(96) NOT NULL DEFAULT '',
            error_message VARCHAR(512) NOT NULL DEFAULT '',
            duration_ms BIGINT NOT NULL DEFAULT 0,
            safe_metadata JSON NULL,
            UNIQUE KEY uq_job_attempts_number (job_id, attempt_number),
            KEY idx_job_attempts_job_started (job_id, started_at DESC, id DESC),
            CONSTRAINT chk_job_attempts_number CHECK (attempt_number >= 1),
            CONSTRAINT chk_job_attempts_duration CHECK (duration_ms >= 0),
            CONSTRAINT chk_job_attempts_outcome CHECK (outcome IN ('running', 'succeeded', 'retry', 'failed', 'cancelled', 'verification_required'))
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
        f"""
        CREATE TABLE IF NOT EXISTS sync_cursors (
            id {ID} PRIMARY KEY,
            user_uid {ID} NOT NULL,
            account_id {ID} NOT NULL,
            mailbox_id {ID} NOT NULL DEFAULT '',
            phase VARCHAR(64) NOT NULL,
            cursor_type VARCHAR(32) NOT NULL DEFAULT 'json',
            cursor_json JSON NULL,
            last_uid BIGINT UNSIGNED NOT NULL DEFAULT 0,
            highest_modseq BIGINT UNSIGNED NOT NULL DEFAULT 0,
            updated_at DOUBLE NOT NULL DEFAULT 0,
            UNIQUE KEY uq_sync_cursors_scope (account_id, mailbox_id, phase),
            KEY idx_sync_cursors_user_account (user_uid, account_id, phase)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
        f"""
        CREATE TABLE IF NOT EXISTS account_runtime_state (
            account_id {ID} PRIMARY KEY,
            user_uid {ID} NOT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'normal',
            idle_status VARCHAR(32) NOT NULL DEFAULT 'disconnected',
            last_activity_at DOUBLE NOT NULL DEFAULT 0,
            last_change_at DOUBLE NOT NULL DEFAULT 0,
            next_reconcile_at DOUBLE NOT NULL DEFAULT 0,
            failure_count INT NOT NULL DEFAULT 0,
            backoff_until DOUBLE NOT NULL DEFAULT 0,
            last_error_class VARCHAR(96) NOT NULL DEFAULT '',
            last_error_message VARCHAR(512) NOT NULL DEFAULT '',
            updated_at DOUBLE NOT NULL DEFAULT 0,
            KEY idx_account_runtime_schedule (status, next_reconcile_at, account_id),
            KEY idx_account_runtime_user (user_uid, status, account_id),
            CONSTRAINT chk_account_runtime_status CHECK (status IN ('active', 'normal', 'quiet', 'degraded', 'auth_required', 'disabled')),
            CONSTRAINT chk_account_runtime_idle CHECK (idle_status IN ('disconnected', 'connecting', 'idling', 'degraded', 'unsupported')),
            CONSTRAINT chk_account_runtime_failures CHECK (failure_count >= 0)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
        f"""
        CREATE TABLE IF NOT EXISTS realtime_events (
            sequence_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            event_id {ID} NOT NULL,
            user_uid {ID} NOT NULL,
            event_type VARCHAR(96) NOT NULL,
            aggregate_type VARCHAR(64) NOT NULL DEFAULT '',
            aggregate_id {ID} NULL,
            payload JSON NOT NULL,
            created_at DOUBLE NOT NULL DEFAULT 0,
            expires_at DOUBLE NOT NULL DEFAULT 0,
            UNIQUE KEY uq_realtime_events_event_id (event_id),
            KEY idx_realtime_events_user_sequence (user_uid, sequence_id),
            KEY idx_realtime_events_expiry (expires_at, sequence_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
        f"""
        CREATE TABLE IF NOT EXISTS notification_events (
            id {ID} PRIMARY KEY,
            user_uid {ID} NOT NULL,
            event_type VARCHAR(96) NOT NULL,
            title VARCHAR(255) NOT NULL,
            summary VARCHAR(1024) NOT NULL DEFAULT '',
            action_path VARCHAR(1024) NOT NULL DEFAULT '',
            account_id {ID} NULL,
            dedupe_key VARCHAR(191) NOT NULL,
            created_at DOUBLE NOT NULL DEFAULT 0,
            read_at DOUBLE NULL,
            dismissed_at DOUBLE NULL,
            UNIQUE KEY uq_notification_events_dedupe (user_uid, dedupe_key),
            KEY idx_notification_events_user_created (user_uid, created_at DESC, id DESC),
            KEY idx_notification_events_user_unread (user_uid, read_at, created_at DESC, id DESC)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
        f"""
        CREATE TABLE IF NOT EXISTS notification_deliveries (
            id {ID} PRIMARY KEY,
            user_uid {ID} NOT NULL,
            notification_event_id {ID} NOT NULL,
            channel_id {ID} NOT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'pending',
            attempt_count INT NOT NULL DEFAULT 0,
            available_at DOUBLE NOT NULL DEFAULT 0,
            delivered_at DOUBLE NULL,
            last_error_class VARCHAR(96) NOT NULL DEFAULT '',
            last_error_message VARCHAR(512) NOT NULL DEFAULT '',
            idempotency_key VARCHAR(191) NOT NULL,
            created_at DOUBLE NOT NULL DEFAULT 0,
            updated_at DOUBLE NOT NULL DEFAULT 0,
            UNIQUE KEY uq_notification_deliveries_idempotency (idempotency_key),
            KEY idx_notification_deliveries_claim (status, available_at, id),
            KEY idx_notification_deliveries_event (notification_event_id, channel_id, id),
            CONSTRAINT chk_notification_deliveries_status CHECK (status IN ('pending', 'sending', 'succeeded', 'retry_wait', 'failed', 'cancelled')),
            CONSTRAINT chk_notification_deliveries_attempts CHECK (attempt_count >= 0)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
        f"""
        CREATE TABLE IF NOT EXISTS drafts (
            id {ID} PRIMARY KEY,
            user_uid {ID} NOT NULL,
            account_id {ID} NOT NULL,
            identity_id {ID} NOT NULL,
            thread_id {ID} NULL,
            reply_to_message_id {ID} NULL,
            subject TEXT NULL,
            body_html_object_sha256 {SHA256} NULL,
            body_text_object_sha256 {SHA256} NULL,
            version BIGINT NOT NULL DEFAULT 1,
            status VARCHAR(32) NOT NULL DEFAULT 'draft',
            scheduled_at DOUBLE NULL,
            send_message_id VARCHAR(998) NOT NULL DEFAULT '',
            created_at DOUBLE NOT NULL DEFAULT 0,
            updated_at DOUBLE NOT NULL DEFAULT 0,
            queued_at DOUBLE NULL,
            sent_at DOUBLE NULL,
            KEY idx_drafts_user_status (user_uid, status, updated_at DESC, id DESC),
            KEY idx_drafts_schedule (status, scheduled_at, id),
            KEY idx_drafts_thread (user_uid, thread_id, updated_at DESC),
            CONSTRAINT chk_drafts_version CHECK (version >= 1),
            CONSTRAINT chk_drafts_status CHECK (status IN ('draft', 'queued', 'sending', 'sent', 'failed', 'cancelled', 'conflict', 'review_required'))
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
        f"""
        CREATE TABLE IF NOT EXISTS draft_recipients (
            id {ID} PRIMARY KEY,
            draft_id {ID} NOT NULL,
            user_uid {ID} NOT NULL,
            recipient_kind VARCHAR(8) NOT NULL,
            address VARCHAR(320) NOT NULL,
            normalized_address VARCHAR(320) NOT NULL,
            display_name VARCHAR(191) NOT NULL DEFAULT '',
            position_index INT NOT NULL DEFAULT 0,
            UNIQUE KEY uq_draft_recipients_address (draft_id, recipient_kind, normalized_address),
            KEY idx_draft_recipients_draft_order (draft_id, recipient_kind, position_index, id),
            CONSTRAINT chk_draft_recipients_kind CHECK (recipient_kind IN ('to', 'cc', 'bcc')),
            CONSTRAINT chk_draft_recipients_position CHECK (position_index >= 0)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
        f"""
        CREATE TABLE IF NOT EXISTS draft_attachments (
            id {ID} PRIMARY KEY,
            draft_id {ID} NOT NULL,
            user_uid {ID} NOT NULL,
            content_sha256 {SHA256} NOT NULL,
            filename VARCHAR(1024) NOT NULL,
            content_type VARCHAR(255) NOT NULL DEFAULT 'application/octet-stream',
            size_bytes BIGINT NOT NULL DEFAULT 0,
            position_index INT NOT NULL DEFAULT 0,
            created_at DOUBLE NOT NULL DEFAULT 0,
            UNIQUE KEY uq_draft_attachments_object (draft_id, content_sha256, filename(191)),
            KEY idx_draft_attachments_draft_order (draft_id, position_index, id),
            KEY idx_draft_attachments_object (content_sha256, user_uid),
            CONSTRAINT chk_draft_attachments_size CHECK (size_bytes >= 0),
            CONSTRAINT chk_draft_attachments_position CHECK (position_index >= 0)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
        f"""
        CREATE TABLE IF NOT EXISTS send_attempts (
            id {ID} PRIMARY KEY,
            user_uid {ID} NOT NULL,
            draft_id {ID} NOT NULL,
            operation_id {ID} NOT NULL,
            account_id {ID} NOT NULL,
            message_id_header VARCHAR(998) NOT NULL,
            attempt_number INT NOT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'sending',
            smtp_response_code INT NULL,
            safe_response VARCHAR(512) NOT NULL DEFAULT '',
            started_at DOUBLE NOT NULL DEFAULT 0,
            finished_at DOUBLE NULL,
            created_at DOUBLE NOT NULL DEFAULT 0,
            UNIQUE KEY uq_send_attempts_operation_attempt (operation_id, attempt_number),
            KEY idx_send_attempts_message (account_id, message_id_header(191), started_at DESC),
            KEY idx_send_attempts_draft (user_uid, draft_id, attempt_number DESC),
            CONSTRAINT chk_send_attempts_number CHECK (attempt_number >= 1),
            CONSTRAINT chk_send_attempts_status CHECK (status IN ('sending', 'sent', 'failed', 'verification_required', 'cancelled'))
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
    ),
)
