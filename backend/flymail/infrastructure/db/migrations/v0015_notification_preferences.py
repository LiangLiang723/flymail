"""Persist safe per-user notification-center preferences."""

from flymail.infrastructure.db.migrations import Migration


ID = "VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin"


MIGRATION = Migration(
    version=15,
    name="notification_preferences",
    statements=(
        f"""
        CREATE TABLE IF NOT EXISTS notification_preferences (
            user_uid {ID} PRIMARY KEY,
            in_app_enabled TINYINT NOT NULL DEFAULT 1,
            external_enabled TINYINT NOT NULL DEFAULT 1,
            include_images TINYINT NOT NULL DEFAULT 0,
            quiet_hours_json JSON NULL,
            event_preferences_json JSON NULL,
            created_at DOUBLE NOT NULL DEFAULT 0,
            updated_at DOUBLE NOT NULL DEFAULT 0,
            CONSTRAINT chk_notification_preferences_flags CHECK (
                in_app_enabled IN (0, 1)
                AND external_enabled IN (0, 1)
                AND include_images IN (0, 1)
            )
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
    ),
)
