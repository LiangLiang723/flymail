"""Persist reliable-send state and exact composed RFC822 source metadata."""

from flymail.infrastructure.db.migrations import Migration


MIGRATION = Migration(
    version=9,
    name="reliable_sender_state",
    statements=(
        """
        ALTER TABLE drafts
        ADD COLUMN send_state VARCHAR(32) NOT NULL DEFAULT 'draft'
            CHECK (send_state IN (
                'draft', 'queued', 'sending', 'sent', 'failed',
                'verification_required', 'review_required', 'cancelled'
            ))
        """,
        """
        ALTER TABLE drafts
        ADD COLUMN composed_object_sha256
            CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL
        """,
        """
        ALTER TABLE drafts
        ADD COLUMN verification_attempts INT NOT NULL DEFAULT 0
            CHECK (verification_attempts >= 0)
        """,
        """
        ALTER TABLE drafts
        ADD INDEX idx_drafts_send_state (
            user_uid, send_state, scheduled_at, id
        )
        """,
    ),
)
