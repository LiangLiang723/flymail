"""Secret-safe security audit persistence for FlyMail V2."""

from __future__ import annotations

import time
from collections.abc import Mapping

import aiomysql

from flymail.domain.ids import new_id
from flymail.repositories.outbox import encode_safe_json, validate_safe_payload


class AuditRepository:
    """Append immutable audit rows without committing the caller transaction."""

    def __init__(self, connection: aiomysql.Connection) -> None:
        self.connection = connection

    async def append(
        self,
        *,
        event_type: str,
        result_code: str,
        request_id: str,
        user_uid: str | None = None,
        actor_user_uid: str | None = None,
        resource_type: str = "",
        resource_id: str | None = None,
        safe_metadata: Mapping[str, object] | None = None,
        now: float | None = None,
    ) -> str:
        normalized_event = str(event_type or "").strip()
        normalized_result = str(result_code or "").strip()
        normalized_request = str(request_id or "").strip()
        if not normalized_event or not normalized_result or not normalized_request:
            raise ValueError("event_type, result_code and request_id are required")
        metadata = dict(safe_metadata or {})
        validate_safe_payload(metadata, path="audit.safe_metadata")
        audit_id = new_id("aud")
        timestamp = float(time.time() if now is None else now)
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO audit_events (
                    id, user_uid, actor_user_uid, event_type,
                    resource_type, resource_id, result_code,
                    request_id, safe_metadata, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    audit_id,
                    str(user_uid or "").strip() or None,
                    str(actor_user_uid or "").strip() or None,
                    normalized_event,
                    str(resource_type or "").strip(),
                    str(resource_id or "").strip() or None,
                    normalized_result,
                    normalized_request,
                    encode_safe_json(metadata),
                    timestamp,
                ),
            )
        return audit_id
