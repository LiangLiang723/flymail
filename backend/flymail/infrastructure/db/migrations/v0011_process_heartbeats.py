"""Persist API and Worker process heartbeats independently from job leases."""

from flymail.infrastructure.db.migrations import Migration


ID = "VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin"


MIGRATION = Migration(
    version=11,
    name="process_heartbeats",
    statements=(
        f"""
        CREATE TABLE IF NOT EXISTS process_heartbeats (
            process_id {ID} PRIMARY KEY,
            role VARCHAR(32) NOT NULL,
            started_at DOUBLE NOT NULL DEFAULT 0,
            heartbeat_at DOUBLE NOT NULL DEFAULT 0,
            updated_at DOUBLE NOT NULL DEFAULT 0,
            KEY idx_process_heartbeats_role_time (
                role, heartbeat_at DESC, process_id
            ),
            CONSTRAINT chk_process_heartbeats_role
                CHECK (role IN ('api', 'worker'))
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
        """,
    ),
)
