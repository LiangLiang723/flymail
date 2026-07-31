"""Stable identifier helpers for FlyMail V2 entities."""

import re
import uuid


_ID_PREFIX_PATTERN = re.compile(r"[a-z][a-z0-9_]{1,15}")


def new_id(prefix: str) -> str:
    """Return a prefixed random identifier suitable for URLs and MySQL keys."""

    normalized = str(prefix or "").strip().lower()
    if not _ID_PREFIX_PATTERN.fullmatch(normalized):
        raise ValueError("invalid id prefix")
    return f"{normalized}_{uuid.uuid4().hex}"
