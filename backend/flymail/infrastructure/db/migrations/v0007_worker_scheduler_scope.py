"""Add explicit account and provider scheduling scope to durable Worker jobs."""

from flymail.infrastructure.db.migrations import Migration


ID = "VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin"


MIGRATION = Migration(
    version=7,
    name="worker_scheduler_scope",
    statements=(
        f"""
        ALTER TABLE worker_jobs
        ADD COLUMN account_id {ID} NULL AFTER user_uid
        """,
        """
        ALTER TABLE worker_jobs
        ADD COLUMN provider_key VARCHAR(64) NOT NULL DEFAULT '' AFTER account_id
        """,
        """
        ALTER TABLE worker_jobs
        ADD INDEX idx_worker_jobs_scheduler (
            queue_name, status, available_at, priority,
            provider_key, account_id, id
        )
        """,
    ),
)
