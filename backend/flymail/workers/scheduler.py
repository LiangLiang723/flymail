"""Weighted-fair in-memory selection for durable FlyMail Worker jobs."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from types import MappingProxyType
from typing import Iterable, Mapping


QUEUE_WEIGHTS = MappingProxyType(
    {
        "interactive": 8,
        "operations": 6,
        "realtime": 6,
        "reconcile": 3,
        "history": 1,
        "maintenance": 1,
    }
)
QUEUE_ORDER = tuple(QUEUE_WEIGHTS)
_ALLOWED_RUNTIME_STATUSES = {"active", "normal", "quiet", "degraded"}


def _required_text(value: str, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{label} is required")
    return normalized


@dataclass(frozen=True, slots=True)
class ReadyJob:
    id: str
    queue_name: str
    priority: int
    available_at: float
    account_id: str | None = None
    provider_key: str | None = None
    account_status: str = "active"
    runtime_status: str = "normal"
    backoff_until: float = 0

    def __post_init__(self) -> None:
        job_id = _required_text(self.id, "job id")
        queue_name = _required_text(self.queue_name, "queue name")
        if queue_name not in QUEUE_WEIGHTS:
            raise ValueError(f"unsupported worker queue: {queue_name}")
        if isinstance(self.priority, bool):
            raise TypeError("priority must be an integer")
        priority = int(self.priority)
        available_at = float(self.available_at)
        backoff_until = float(self.backoff_until)
        if not math.isfinite(available_at) or not math.isfinite(backoff_until):
            raise ValueError("scheduler timestamps must be finite")
        account_id = str(self.account_id or "").strip() or None
        provider_key = str(self.provider_key or "").strip().casefold() or None
        if bool(account_id) != bool(provider_key):
            raise ValueError("account_id and provider_key must be supplied together")
        account_status = str(self.account_status or "").strip().casefold()
        runtime_status = str(self.runtime_status or "").strip().casefold()
        object.__setattr__(self, "id", job_id)
        object.__setattr__(self, "queue_name", queue_name)
        object.__setattr__(self, "priority", priority)
        object.__setattr__(self, "available_at", available_at)
        object.__setattr__(self, "account_id", account_id)
        object.__setattr__(self, "provider_key", provider_key)
        object.__setattr__(self, "account_status", account_status)
        object.__setattr__(self, "runtime_status", runtime_status)
        object.__setattr__(self, "backoff_until", backoff_until)


@dataclass(frozen=True, slots=True)
class ClaimRequest:
    job_id: str
    queue_name: str
    account_id: str | None = None
    provider_key: str | None = None

    def __post_init__(self) -> None:
        job_id = _required_text(self.job_id, "job id")
        queue_name = _required_text(self.queue_name, "queue name")
        if queue_name not in QUEUE_WEIGHTS:
            raise ValueError(f"unsupported worker queue: {queue_name}")
        account_id = str(self.account_id or "").strip() or None
        provider_key = str(self.provider_key or "").strip().casefold() or None
        if bool(account_id) != bool(provider_key):
            raise ValueError("account_id and provider_key must be supplied together")
        object.__setattr__(self, "job_id", job_id)
        object.__setattr__(self, "queue_name", queue_name)
        object.__setattr__(self, "account_id", account_id)
        object.__setattr__(self, "provider_key", provider_key)


class FairScheduler:
    """Select ready jobs with smooth weighted round-robin and concurrency caps.

    Queue weights affect only the current claim mix. Durable ordering, readiness,
    leases, and retries remain in MySQL. Scheduler state is intentionally small;
    losing it on process restart changes only the starting point of the fair cycle.
    """

    def __init__(
        self,
        *,
        global_slots: int = 8,
        per_account_limit: int = 2,
        provider_limits: Mapping[str, int] | None = None,
        queue_weights: Mapping[str, int] | None = None,
    ) -> None:
        if isinstance(global_slots, bool) or int(global_slots) < 1:
            raise ValueError("global_slots must be at least 1")
        if isinstance(per_account_limit, bool) or int(per_account_limit) < 1:
            raise ValueError("per_account_limit must be at least 1")
        weights = dict(QUEUE_WEIGHTS if queue_weights is None else queue_weights)
        if set(weights) != set(QUEUE_WEIGHTS):
            raise ValueError("queue weights must define every stable worker queue")
        for queue_name, weight in weights.items():
            if isinstance(weight, bool) or int(weight) < 1:
                raise ValueError(f"queue weight must be positive: {queue_name}")
            weights[queue_name] = int(weight)
        limits: dict[str, int] = {}
        for raw_provider, raw_limit in dict(provider_limits or {}).items():
            provider = _required_text(raw_provider, "provider key").casefold()
            if isinstance(raw_limit, bool) or int(raw_limit) < 1:
                raise ValueError("provider limits must be at least 1")
            limits[provider] = int(raw_limit)
        self.global_slots = int(global_slots)
        self.per_account_limit = int(per_account_limit)
        self.provider_limits = MappingProxyType(limits)
        self.queue_weights = MappingProxyType(weights)
        self._current_weights = {queue_name: 0 for queue_name in QUEUE_ORDER}

    def next_claims(
        self,
        candidates: Iterable[ReadyJob],
        *,
        in_flight: Iterable[ClaimRequest] = (),
        provider_cooldowns: Mapping[str, float] | None = None,
        now: float | None = None,
    ) -> list[ClaimRequest]:
        timestamp = float(time.time() if now is None else now)
        if not math.isfinite(timestamp):
            raise ValueError("scheduler time must be finite")
        cooldowns = {
            str(provider or "").strip().casefold(): float(until)
            for provider, until in dict(provider_cooldowns or {}).items()
            if str(provider or "").strip()
        }
        if any(not math.isfinite(until) for until in cooldowns.values()):
            raise ValueError("provider cooldowns must be finite")

        active = tuple(in_flight)
        available_slots = max(self.global_slots - len(active), 0)
        if available_slots == 0:
            return []
        account_counts: dict[str, int] = {}
        provider_counts: dict[str, int] = {}
        for claim in active:
            if claim.account_id:
                account_counts[claim.account_id] = account_counts.get(claim.account_id, 0) + 1
            if claim.provider_key:
                provider_counts[claim.provider_key] = provider_counts.get(claim.provider_key, 0) + 1

        queues: dict[str, list[ReadyJob]] = {queue: [] for queue in QUEUE_ORDER}
        seen_ids: set[str] = set()
        for candidate in candidates:
            if not isinstance(candidate, ReadyJob):
                raise TypeError("scheduler candidates must be ReadyJob values")
            if candidate.id in seen_ids:
                continue
            seen_ids.add(candidate.id)
            if candidate.available_at > timestamp:
                continue
            queues[candidate.queue_name].append(candidate)
        for queue in queues.values():
            queue.sort(key=lambda item: (item.priority, item.available_at, item.id))

        selected: list[ClaimRequest] = []
        while len(selected) < available_slots:
            eligible_by_queue: dict[str, tuple[int, ReadyJob]] = {}
            for queue_name in QUEUE_ORDER:
                for index, candidate in enumerate(queues[queue_name]):
                    if self._eligible(
                        candidate,
                        timestamp=timestamp,
                        cooldowns=cooldowns,
                        account_counts=account_counts,
                        provider_counts=provider_counts,
                    ):
                        eligible_by_queue[queue_name] = (index, candidate)
                        break
            if not eligible_by_queue:
                break

            total_weight = sum(self.queue_weights[queue] for queue in eligible_by_queue)
            for queue_name in eligible_by_queue:
                self._current_weights[queue_name] += self.queue_weights[queue_name]
            selected_queue = max(
                eligible_by_queue,
                key=lambda queue: (
                    self._current_weights[queue],
                    -QUEUE_ORDER.index(queue),
                ),
            )
            self._current_weights[selected_queue] -= total_weight
            index, candidate = eligible_by_queue[selected_queue]
            queues[selected_queue].pop(index)
            request = ClaimRequest(
                job_id=candidate.id,
                queue_name=candidate.queue_name,
                account_id=candidate.account_id,
                provider_key=candidate.provider_key,
            )
            selected.append(request)
            if request.account_id:
                account_counts[request.account_id] = account_counts.get(request.account_id, 0) + 1
            if request.provider_key:
                provider_counts[request.provider_key] = provider_counts.get(request.provider_key, 0) + 1
        return selected

    def _eligible(
        self,
        candidate: ReadyJob,
        *,
        timestamp: float,
        cooldowns: Mapping[str, float],
        account_counts: Mapping[str, int],
        provider_counts: Mapping[str, int],
    ) -> bool:
        if candidate.account_id is None:
            return True
        if candidate.account_status != "active":
            return False
        if candidate.runtime_status not in _ALLOWED_RUNTIME_STATUSES:
            return False
        if candidate.backoff_until > timestamp:
            return False
        if account_counts.get(candidate.account_id, 0) >= self.per_account_limit:
            return False
        assert candidate.provider_key is not None
        if cooldowns.get(candidate.provider_key, 0) > timestamp:
            return False
        provider_limit = self.provider_limits.get(candidate.provider_key, self.global_slots)
        if provider_counts.get(candidate.provider_key, 0) >= provider_limit:
            return False
        return True
