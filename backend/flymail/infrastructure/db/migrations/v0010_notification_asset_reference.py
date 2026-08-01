"""Attach temporary notification assets through internal content-reference IDs."""

from flymail.infrastructure.db.migrations import Migration


ID = "VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin"


MIGRATION = Migration(
    version=10,
    name="notification_asset_reference",
    statements=(
        f"""
        ALTER TABLE notification_events
        ADD COLUMN notification_asset_id {ID} NULL
        """,
        """
        ALTER TABLE notification_events
        ADD INDEX idx_notification_events_asset (notification_asset_id, id)
        """,
    ),
)
