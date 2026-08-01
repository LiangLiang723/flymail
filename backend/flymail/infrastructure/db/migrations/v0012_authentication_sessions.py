"""Add password-versioned sessions, CSRF hashes, and login failure windows."""

from flymail.infrastructure.db.migrations import Migration


SHA256 = "CHAR(64) CHARACTER SET ascii COLLATE ascii_bin"


MIGRATION = Migration(
    version=12,
    name="authentication_sessions",
    statements=(
        """
        ALTER TABLE user_sessions
        ADD COLUMN password_version BIGINT NOT NULL DEFAULT 1
        AFTER token_hash
        """,
        f"""
        ALTER TABLE user_sessions
        ADD COLUMN csrf_token_hash {SHA256} NOT NULL DEFAULT ''
        AFTER password_version
        """,
        f"""
        CREATE TABLE IF NOT EXISTS login_rate_limits (
            principal_hash {SHA256} NOT NULL,
            source_hash {SHA256} NOT NULL,
            failure_count INT NOT NULL DEFAULT 0,
            window_started_at DOUBLE NOT NULL DEFAULT 0,
            blocked_until DOUBLE NOT NULL DEFAULT 0,
            updated_at DOUBLE NOT NULL DEFAULT 0,
            PRIMARY KEY (principal_hash, source_hash),
            KEY idx_login_rate_limits_blocked (blocked_until, updated_at),
            CONSTRAINT chk_login_rate_limits_failures
                CHECK (failure_count >= 0)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
    ),
)
