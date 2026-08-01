"""Add the bounded subject fallback lookup index used by thread ingestion."""

from flymail.infrastructure.db.migrations import Migration


MIGRATION = Migration(
    version=6,
    name="message_thread_fallback_index",
    statements=(
        """
        ALTER TABLE messages
        ADD INDEX idx_messages_subject_fallback (
            user_uid,
            normalized_subject(191),
            received_at DESC,
            id DESC
        )
        """,
    ),
)
