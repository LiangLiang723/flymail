"""V2 identity, account, and configuration schema."""

from flymail.infrastructure.db.migrations import Migration


ID = "VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin"
SHA256 = "CHAR(64) CHARACTER SET ascii COLLATE ascii_bin"


MIGRATION = Migration(
    version=1,
    name="identity_and_configuration",
    statements=(
        f"""
        CREATE TABLE IF NOT EXISTS users (
            id {ID} PRIMARY KEY,
            username VARCHAR(191) NOT NULL,
            password_hash VARCHAR(512) NOT NULL,
            role VARCHAR(32) NOT NULL,
            enabled TINYINT NOT NULL DEFAULT 1,
            password_version BIGINT NOT NULL DEFAULT 1,
            created_at DOUBLE NOT NULL DEFAULT 0,
            updated_at DOUBLE NOT NULL DEFAULT 0,
            UNIQUE KEY uq_users_username (username),
            CONSTRAINT chk_users_role CHECK (role IN ('admin', 'user'))
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
        f"""
        CREATE TABLE IF NOT EXISTS user_profiles (
            user_uid {ID} PRIMARY KEY,
            nickname VARCHAR(191) NOT NULL DEFAULT '',
            avatar_object_sha256 {SHA256} NULL,
            created_at DOUBLE NOT NULL DEFAULT 0,
            updated_at DOUBLE NOT NULL DEFAULT 0,
            KEY idx_user_profiles_avatar (avatar_object_sha256)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
        f"""
        CREATE TABLE IF NOT EXISTS user_sessions (
            id {ID} PRIMARY KEY,
            user_uid {ID} NOT NULL,
            token_hash {SHA256} NOT NULL,
            expires_at DOUBLE NOT NULL,
            revoked_at DOUBLE NULL,
            last_seen_at DOUBLE NOT NULL DEFAULT 0,
            created_at DOUBLE NOT NULL DEFAULT 0,
            UNIQUE KEY uq_user_sessions_token (token_hash),
            KEY idx_user_sessions_user_expiry (user_uid, expires_at, revoked_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
        f"""
        CREATE TABLE IF NOT EXISTS user_settings (
            user_uid {ID} PRIMARY KEY,
            body_cache_quota_bytes BIGINT NOT NULL DEFAULT 5368709120,
            attachment_cache_quota_bytes BIGINT NOT NULL DEFAULT 2147483648,
            ui_preferences JSON NULL,
            compose_preferences JSON NULL,
            remote_image_policy JSON NULL,
            created_at DOUBLE NOT NULL DEFAULT 0,
            updated_at DOUBLE NOT NULL DEFAULT 0,
            CONSTRAINT chk_user_settings_body_quota CHECK (body_cache_quota_bytes >= 0),
            CONSTRAINT chk_user_settings_attachment_quota CHECK (attachment_cache_quota_bytes >= 0)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
        f"""
        CREATE TABLE IF NOT EXISTS audit_events (
            id {ID} PRIMARY KEY,
            user_uid {ID} NULL,
            actor_user_uid {ID} NULL,
            event_type VARCHAR(96) NOT NULL,
            resource_type VARCHAR(64) NOT NULL DEFAULT '',
            resource_id {ID} NULL,
            result_code VARCHAR(64) NOT NULL DEFAULT '',
            request_id {ID} NULL,
            safe_metadata JSON NULL,
            created_at DOUBLE NOT NULL DEFAULT 0,
            KEY idx_audit_events_user_created (user_uid, created_at DESC, id DESC),
            KEY idx_audit_events_actor_created (actor_user_uid, created_at DESC, id DESC)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
        f"""
        CREATE TABLE IF NOT EXISTS contacts (
            id {ID} PRIMARY KEY,
            user_uid {ID} NOT NULL,
            display_name VARCHAR(191) NOT NULL DEFAULT '',
            normalized_name VARCHAR(191) NOT NULL DEFAULT '',
            primary_email VARCHAR(320) NOT NULL DEFAULT '',
            normalized_email VARCHAR(320) NOT NULL DEFAULT '',
            emails_json JSON NOT NULL,
            phone VARCHAR(64) NOT NULL DEFAULT '',
            company VARCHAR(191) NOT NULL DEFAULT '',
            remark TEXT NULL,
            group_name VARCHAR(191) NOT NULL DEFAULT '',
            avatar_object_sha256 {SHA256} NULL,
            created_at DOUBLE NOT NULL DEFAULT 0,
            updated_at DOUBLE NOT NULL DEFAULT 0,
            UNIQUE KEY uq_contacts_user_email (user_uid, normalized_email),
            KEY idx_contacts_user_name (user_uid, normalized_name, id),
            KEY idx_contacts_avatar (avatar_object_sha256)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
        f"""
        CREATE TABLE IF NOT EXISTS authorized_storage_roots (
            id {ID} PRIMARY KEY,
            user_uid {ID} NULL,
            label VARCHAR(191) NOT NULL,
            root_path VARCHAR(1024) NOT NULL,
            visibility_scope VARCHAR(32) NOT NULL DEFAULT 'user',
            enabled TINYINT NOT NULL DEFAULT 1,
            created_by {ID} NOT NULL,
            created_at DOUBLE NOT NULL DEFAULT 0,
            updated_at DOUBLE NOT NULL DEFAULT 0,
            KEY idx_storage_roots_user_enabled (user_uid, enabled, label),
            CONSTRAINT chk_storage_roots_scope CHECK (visibility_scope IN ('user', 'all'))
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
        f"""
        CREATE TABLE IF NOT EXISTS mail_accounts (
            id {ID} PRIMARY KEY,
            user_uid {ID} NOT NULL,
            provider_key VARCHAR(64) NOT NULL,
            email VARCHAR(320) NOT NULL,
            normalized_email VARCHAR(320) NOT NULL,
            display_name VARCHAR(191) NOT NULL DEFAULT '',
            remark VARCHAR(255) NOT NULL DEFAULT '',
            group_name VARCHAR(191) NOT NULL DEFAULT '',
            status VARCHAR(32) NOT NULL DEFAULT 'pending',
            endpoint_config JSON NULL,
            icon_mode VARCHAR(32) NOT NULL DEFAULT 'provider',
            icon_value VARCHAR(191) NOT NULL DEFAULT '',
            icon_object_sha256 {SHA256} NULL,
            poll_interval_seconds INT NOT NULL DEFAULT 300,
            created_at DOUBLE NOT NULL DEFAULT 0,
            updated_at DOUBLE NOT NULL DEFAULT 0,
            UNIQUE KEY uq_mail_accounts_user_email (user_uid, normalized_email),
            KEY idx_mail_accounts_user_status (user_uid, status, id),
            KEY idx_mail_accounts_icon (icon_object_sha256),
            CONSTRAINT chk_mail_accounts_status CHECK (status IN ('pending', 'active', 'disabled', 'auth_required', 'deleting')),
            CONSTRAINT chk_mail_accounts_poll CHECK (poll_interval_seconds BETWEEN 5 AND 3600)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
        f"""
        CREATE TABLE IF NOT EXISTS mail_identities (
            id {ID} PRIMARY KEY,
            user_uid {ID} NOT NULL,
            account_id {ID} NOT NULL,
            from_address VARCHAR(320) NOT NULL,
            normalized_from_address VARCHAR(320) NOT NULL,
            display_name VARCHAR(191) NOT NULL DEFAULT '',
            reply_to VARCHAR(320) NOT NULL DEFAULT '',
            signature_html LONGTEXT NULL,
            signature_text LONGTEXT NULL,
            is_default TINYINT NOT NULL DEFAULT 0,
            is_verified TINYINT NOT NULL DEFAULT 0,
            created_at DOUBLE NOT NULL DEFAULT 0,
            updated_at DOUBLE NOT NULL DEFAULT 0,
            UNIQUE KEY uq_mail_identities_account_from (account_id, normalized_from_address),
            KEY idx_mail_identities_user_account (user_uid, account_id, is_default, id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
        f"""
        CREATE TABLE IF NOT EXISTS provider_credentials (
            id {ID} PRIMARY KEY,
            user_uid {ID} NOT NULL,
            account_id {ID} NOT NULL,
            credential_type VARCHAR(32) NOT NULL,
            algorithm VARCHAR(32) NOT NULL,
            key_version INT NOT NULL DEFAULT 1,
            nonce VARBINARY(64) NOT NULL,
            ciphertext LONGBLOB NOT NULL,
            auth_tag VARBINARY(64) NULL,
            expires_at DOUBLE NULL,
            credential_version BIGINT NOT NULL DEFAULT 1,
            created_at DOUBLE NOT NULL DEFAULT 0,
            updated_at DOUBLE NOT NULL DEFAULT 0,
            UNIQUE KEY uq_provider_credentials_account (account_id),
            KEY idx_provider_credentials_user (user_uid, account_id),
            CONSTRAINT chk_provider_credentials_type CHECK (credential_type IN ('password', 'authorization_code', 'oauth'))
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
        f"""
        CREATE TABLE IF NOT EXISTS oauth_authorization_states (
            id {ID} PRIMARY KEY,
            user_uid {ID} NOT NULL,
            session_id {ID} NOT NULL,
            provider_key VARCHAR(64) NOT NULL,
            account_draft_id {ID} NULL,
            state_hash {SHA256} NOT NULL,
            pkce_algorithm VARCHAR(32) NOT NULL DEFAULT 'S256',
            pkce_key_version INT NOT NULL DEFAULT 1,
            pkce_nonce VARBINARY(64) NOT NULL,
            pkce_ciphertext LONGBLOB NOT NULL,
            pkce_auth_tag VARBINARY(64) NULL,
            redirect_uri VARCHAR(2048) NOT NULL,
            expires_at DOUBLE NOT NULL,
            consumed_at DOUBLE NULL,
            created_at DOUBLE NOT NULL DEFAULT 0,
            UNIQUE KEY uq_oauth_states_hash (state_hash),
            KEY idx_oauth_states_user_expiry (user_uid, expires_at, consumed_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
        f"""
        CREATE TABLE IF NOT EXISTS outbound_proxy_configs (
            id {ID} PRIMARY KEY,
            user_uid {ID} NOT NULL,
            account_id {ID} NULL,
            traffic_scope VARCHAR(32) NOT NULL,
            proxy_scheme VARCHAR(16) NOT NULL,
            host VARCHAR(255) NOT NULL,
            port INT NOT NULL,
            username VARCHAR(255) NOT NULL DEFAULT '',
            password_algorithm VARCHAR(32) NULL,
            password_key_version INT NULL,
            password_nonce VARBINARY(64) NULL,
            password_ciphertext LONGBLOB NULL,
            password_auth_tag VARBINARY(64) NULL,
            enabled TINYINT NOT NULL DEFAULT 1,
            created_at DOUBLE NOT NULL DEFAULT 0,
            updated_at DOUBLE NOT NULL DEFAULT 0,
            KEY idx_proxy_configs_user_scope (user_uid, traffic_scope, account_id, enabled),
            CONSTRAINT chk_proxy_configs_scope CHECK (traffic_scope IN ('account', 'oauth', 'notifications')),
            CONSTRAINT chk_proxy_configs_scheme CHECK (proxy_scheme IN ('http')),
            CONSTRAINT chk_proxy_configs_port CHECK (port BETWEEN 1 AND 65535)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
        f"""
        CREATE TABLE IF NOT EXISTS notification_channels (
            id {ID} PRIMARY KEY,
            user_uid {ID} NOT NULL,
            channel_key VARCHAR(32) NOT NULL,
            display_name VARCHAR(191) NOT NULL,
            enabled TINYINT NOT NULL DEFAULT 1,
            public_config JSON NULL,
            secret_algorithm VARCHAR(32) NULL,
            secret_key_version INT NULL,
            secret_nonce VARBINARY(64) NULL,
            secret_ciphertext LONGBLOB NULL,
            secret_auth_tag VARBINARY(64) NULL,
            use_proxy TINYINT NOT NULL DEFAULT 0,
            created_at DOUBLE NOT NULL DEFAULT 0,
            updated_at DOUBLE NOT NULL DEFAULT 0,
            UNIQUE KEY uq_notification_channels_name (user_uid, channel_key, display_name),
            KEY idx_notification_channels_user_enabled (user_uid, enabled, channel_key),
            CONSTRAINT chk_notification_channels_key CHECK (channel_key IN ('in_app', 'bark', 'telegram', 'wecom', 'dingtalk', 'feishu', 'generic_webhook'))
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
        f"""
        CREATE TABLE IF NOT EXISTS notification_image_publishers (
            id {ID} PRIMARY KEY,
            user_uid {ID} NOT NULL,
            publisher_key VARCHAR(32) NOT NULL,
            display_name VARCHAR(191) NOT NULL,
            endpoint_url VARCHAR(2048) NOT NULL,
            enabled TINYINT NOT NULL DEFAULT 1,
            public_config JSON NULL,
            secret_algorithm VARCHAR(32) NULL,
            secret_key_version INT NULL,
            secret_nonce VARBINARY(64) NULL,
            secret_ciphertext LONGBLOB NULL,
            secret_auth_tag VARBINARY(64) NULL,
            created_at DOUBLE NOT NULL DEFAULT 0,
            updated_at DOUBLE NOT NULL DEFAULT 0,
            UNIQUE KEY uq_notification_publishers_name (user_uid, publisher_key, display_name),
            KEY idx_notification_publishers_user_enabled (user_uid, enabled, publisher_key),
            CONSTRAINT chk_notification_publishers_key CHECK (publisher_key IN ('flymail_imgbed', 'generic_https'))
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
        f"""
        CREATE TABLE IF NOT EXISTS notification_rules (
            id {ID} PRIMARY KEY,
            user_uid {ID} NOT NULL,
            event_type VARCHAR(96) NOT NULL,
            channel_id {ID} NOT NULL,
            image_publisher_id {ID} NULL,
            enabled TINYINT NOT NULL DEFAULT 1,
            filter_json JSON NULL,
            dedupe_window_seconds INT NOT NULL DEFAULT 0,
            created_at DOUBLE NOT NULL DEFAULT 0,
            updated_at DOUBLE NOT NULL DEFAULT 0,
            UNIQUE KEY uq_notification_rules_event_channel (user_uid, event_type, channel_id),
            KEY idx_notification_rules_user_enabled (user_uid, enabled, event_type),
            CONSTRAINT chk_notification_rules_window CHECK (dedupe_window_seconds >= 0)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
    ),
)
