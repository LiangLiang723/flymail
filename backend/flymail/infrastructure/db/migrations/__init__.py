"""FlyMail V2 versioned database migration contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)
