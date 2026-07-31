"""Transactional Outbox repository with secret-safe JSON payload validation."""

from __future__ import annotations

import json
import math
import time
from collections.abc import Mapping

import aiomysql

from flymail.domain.ids import new_id
from flymail.repositories.base import TenantContext


_FORBIDDEN_KEYS = {
    "password",
    "token",
    "secret",
    "authorization",
    "body_html",
}


def _required_text(value: str, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{label} is required")
    return normalized


def validate_safe_payload(value, *, path: str = "event") -> None:
    """Reject secrets, raw bytes, non-finite numbers, and non-JSON values."""

    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            if not isinstance(raw_key, str):
                raise ValueError(f"{path} keys must be strings")
            normalized_key = raw_key.strip().casefold()
            if normalized_key in _FORBIDDEN_KEYS:
                raise ValueError(f"unsafe outbox payload key: {raw_key}")
            validate_safe_payload(child, path=f"{path}.{raw_key}")
        return
    if isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            validate_safe_payload(child, path=f"{path}[{index}]")
        return
    if isinstance(value, (bytes, bytearray, memoryview)):
        raise ValueError(f"raw bytes are not allowed in {path}")
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"non-finite numbers are not allowed in {path}")
        return
    raise ValueError(f"unsupported JSON value in {path}: {type(value).__name__}")


def encode_safe_json(value: Mapping) -> str:
    validate_safe_payload(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


class OutboxRepository:
    """Append Outbox rows on the caller-owned database transaction."""

    def __init__(
        self,
        connection: aiomysql.Connection,
        tenant: TenantContext,
        *,
        trace_id: str | None = None,
    ) -> None:
        self.connection = connection
        self.tenant = tenant
        self.trace_id = _required_text(trace_id or new_id("trc"), "trace_id")

    async def append(
        self,
        event_type: str,
        aggregate_id: str,
        payload: dict,
        *,
        aggregate_type: str | None = None,
        now: float | None = None,
    ) -> str:
        normalized_event_type = _required_text(event_type, "event_type")
        normalized_aggregate_id = _required_text(aggregate_id, "aggregate_id")
        if not isinstance(payload, dict):
            raise ValueError("outbox event payload must be an object")
        validate_safe_payload(payload)

        timestamp = float(time.time() if now is None else now)
        normalized_aggregate_type = _required_text(
            aggregate_type or normalized_event_type.split(".", 1)[0] or "domain",
            "aggregate_type",
        )
        envelope = {
            "schema_version": 1,
            "user_uid": self.tenant.user_uid,
            "event": payload,
            "trace_id": self.trace_id,
            "created_at": timestamp,
        }
        event_id = new_id("evt")
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO outbox_events (
                    id, user_uid, aggregate_type, aggregate_id, event_type,
                    payload, created_at, published_at, publish_attempts,
                    last_error_class, last_error_message
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, NULL, 0, '', '')
                """,
                (
                    event_id,
                    self.tenant.user_uid,
                    normalized_aggregate_type,
                    normalized_aggregate_id,
                    normalized_event_type,
                    encode_safe_json(envelope),
                    timestamp,
                ),
            )
        return event_id
