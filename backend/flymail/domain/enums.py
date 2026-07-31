"""Persisted FlyMail V2 state enums.

The string values are database and API contracts. Change them only through a
versioned migration and a reviewed compatibility decision.
"""

from enum import Enum


class StringEnum(str, Enum):
    """String enum whose serialized value is stable and explicit."""


class JobStatus(StringEnum):
    PENDING = "pending"
    LEASED = "leased"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    RETRY_WAIT = "retry_wait"
    FAILED = "failed"
    CANCELLED = "cancelled"


class OperationStatus(StringEnum):
    PENDING = "pending"
    APPLYING = "applying"
    SYNCED = "synced"
    RETRY_WAIT = "retry_wait"
    REVIEW_REQUIRED = "review_required"
    CONFLICT = "conflict"
    FAILED = "failed"
    CANCELLED = "cancelled"


class BodyCacheState(StringEnum):
    NOT_REQUESTED = "not_requested"
    QUEUED = "queued"
    FETCHING = "fetching"
    READY = "ready"
    EVICTED = "evicted"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


class ObjectKind(StringEnum):
    BODY_HTML = "body_html"
    BODY_TEXT = "body_text"
    INLINE_IMAGE = "inline_image"
    ATTACHMENT = "attachment"
    RAW_EML = "raw_eml"
    DRAFT_ATTACHMENT = "draft_attachment"
    USER_AVATAR = "user_avatar"
    ACCOUNT_ICON = "account_icon"
    CONTACT_AVATAR = "contact_avatar"
    NOTIFICATION_ASSET = "notification_asset"


class AccountRuntimeStatus(StringEnum):
    ACTIVE = "active"
    NORMAL = "normal"
    QUIET = "quiet"
    DEGRADED = "degraded"
    AUTH_REQUIRED = "auth_required"
    DISABLED = "disabled"
