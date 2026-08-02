"""Low-cardinality Worker timing and byte counters."""

from __future__ import annotations

import math
from dataclasses import dataclass


def _duration(value: float) -> float:
    number = float(value)
    if not math.isfinite(number):
        return 0.0
    return round(min(max(number, 0.0), 24 * 60 * 60 * 1000.0), 3)


def _counter(value: int) -> int:
    if isinstance(value, bool):
        return 0
    return min(max(int(value), 0), 2**63 - 1)


@dataclass(slots=True)
class JobTiming:
    queue_wait_ms: float = 0.0
    retries: int = 0
    execution_ms: float = 0.0
    bytes_in: int = 0
    bytes_out: int = 0
    result_count: int = 0

    def __post_init__(self) -> None:
        self.queue_wait_ms = _duration(self.queue_wait_ms)
        self.retries = _counter(self.retries)
        self.execution_ms = _duration(self.execution_ms)
        self.bytes_in = _counter(self.bytes_in)
        self.bytes_out = _counter(self.bytes_out)
        self.result_count = _counter(self.result_count)

    def record_execution(self, duration_ms: float) -> None:
        self.execution_ms = _duration(duration_ms)

    def add_bytes_in(self, value: int) -> None:
        self.bytes_in = _counter(self.bytes_in + _counter(value))

    def add_bytes_out(self, value: int) -> None:
        self.bytes_out = _counter(self.bytes_out + _counter(value))

    def add_results(self, value: int) -> None:
        self.result_count = _counter(self.result_count + _counter(value))

    def add_retry(self) -> None:
        self.retries = _counter(self.retries + 1)

    def snapshot(self) -> dict[str, float | int]:
        return {
            "queue_wait_ms": self.queue_wait_ms,
            "execution_ms": self.execution_ms,
            "retries": self.retries,
            "bytes_in": self.bytes_in,
            "bytes_out": self.bytes_out,
            "result_count": self.result_count,
        }


__all__ = ["JobTiming"]
