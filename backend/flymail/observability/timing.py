"""Bounded request timing snapshots for low-cardinality diagnostics."""

from __future__ import annotations

import math
import time
from collections.abc import Callable


def _milliseconds(value: float) -> float:
    number = float(value)
    if not math.isfinite(number):
        return 0.0
    return round(min(max(number, 0.0), 24 * 60 * 60 * 1000.0), 3)


class RequestTiming:
    """Collect additive request phases without recording request content."""

    __slots__ = (
        "_perf_counter",
        "_started",
        "_finished",
        "_total_ms",
        "_db_ms",
        "_object_ms",
        "_serialize_ms",
    )

    def __init__(self, *, perf_counter: Callable[[], float] = time.perf_counter) -> None:
        self._perf_counter = perf_counter
        self._started = float(perf_counter())
        self._finished = False
        self._total_ms = 0.0
        self._db_ms = 0.0
        self._object_ms = 0.0
        self._serialize_ms = 0.0

    def record_db(self, duration_ms: float) -> None:
        self._db_ms = _milliseconds(self._db_ms + _milliseconds(duration_ms))

    def record_object(self, duration_ms: float) -> None:
        self._object_ms = _milliseconds(self._object_ms + _milliseconds(duration_ms))

    def record_serialize(self, duration_ms: float) -> None:
        self._serialize_ms = _milliseconds(
            self._serialize_ms + _milliseconds(duration_ms)
        )

    def finish(self) -> dict[str, float]:
        if not self._finished:
            self._total_ms = _milliseconds(
                (float(self._perf_counter()) - self._started) * 1000.0
            )
            self._finished = True
        return self.snapshot()

    def snapshot(self) -> dict[str, float]:
        return {
            "total_ms": self._total_ms,
            "db_ms": self._db_ms,
            "object_ms": self._object_ms,
            "serialize_ms": self._serialize_ms,
        }

    def server_timing(self) -> str:
        values = self.snapshot()
        return (
            f"total;dur={values['total_ms']:.3f}, "
            f"db;dur={values['db_ms']:.3f}, "
            f"object;dur={values['object_ms']:.3f}, "
            f"serialize;dur={values['serialize_ms']:.3f}"
        )


__all__ = ["RequestTiming"]
