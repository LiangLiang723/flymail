"""Add the ordered durable-job claim index as an incremental migration."""

from flymail.infrastructure.db.migrations import Migration


MIGRATION = Migration(
    version=5,
    name="job_claim_order",
    statements=(
        """
        ALTER TABLE worker_jobs
        ADD INDEX idx_worker_jobs_claim_order (
            queue_name, priority, available_at, id, status
        )
        """,
    ),
)
