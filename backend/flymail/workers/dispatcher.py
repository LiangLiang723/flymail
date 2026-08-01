"""Safe registry and outcome mapping for durable Worker job handlers."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from flymail.providers.errors import ProviderError
from flymail.repositories.jobs import LeasedJob


_ACTIONS = {"complete", "retry", "fail"}


def _required_text(value: str, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{label} is required")
    return normalized


def _freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class JobContext:
    job_id: str
    user_uid: str | None
    account_id: str | None
    provider_key: str | None
    queue_name: str
    worker_id: str
    attempt_count: int
    stop_event: asyncio.Event

    def __post_init__(self) -> None:
        object.__setattr__(self, "job_id", _required_text(self.job_id, "job_id"))
        object.__setattr__(self, "queue_name", _required_text(self.queue_name, "queue_name"))
        object.__setattr__(self, "worker_id", _required_text(self.worker_id, "worker_id"))
        if not isinstance(self.stop_event, asyncio.Event):
            raise TypeError("stop_event must be asyncio.Event")
        if isinstance(self.attempt_count, bool) or int(self.attempt_count) < 1:
            raise ValueError("attempt_count must be at least 1")
        object.__setattr__(self, "attempt_count", int(self.attempt_count))


@dataclass(frozen=True, slots=True)
class JobOutcome:
    action: str
    error_class: str = ""
    error_message: str = ""
    retry_base_seconds: int = 5
    retry_max_seconds: int = 300
    retry_jitter_seconds: int = 0

    def __post_init__(self) -> None:
        action = str(self.action or "").strip().casefold()
        if action not in _ACTIONS:
            raise ValueError("unsupported job outcome action")
        error_class = str(self.error_class or "").replace("\x00", "")[:96]
        error_message = str(self.error_message or "").replace("\x00", "")[:512]
        base = int(self.retry_base_seconds)
        maximum = int(self.retry_max_seconds)
        jitter = int(self.retry_jitter_seconds)
        if base < 0 or maximum < 0 or jitter < 0:
            raise ValueError("retry timing must be non-negative")
        if action == "retry" and not error_class:
            raise ValueError("retry outcomes require error_class")
        if action == "fail" and not error_class:
            raise ValueError("failed outcomes require error_class")
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "error_class", error_class)
        object.__setattr__(self, "error_message", error_message)
        object.__setattr__(self, "retry_base_seconds", base)
        object.__setattr__(self, "retry_max_seconds", maximum)
        object.__setattr__(self, "retry_jitter_seconds", jitter)

    @classmethod
    def success(cls) -> "JobOutcome":
        return cls("complete")

    @classmethod
    def retry(
        cls,
        error_class: str,
        error_message: str = "worker handler failed",
        *,
        base_seconds: int = 5,
        max_seconds: int = 300,
        jitter_seconds: int = 0,
    ) -> "JobOutcome":
        return cls(
            "retry",
            error_class=error_class,
            error_message=error_message,
            retry_base_seconds=base_seconds,
            retry_max_seconds=max_seconds,
            retry_jitter_seconds=jitter_seconds,
        )

    @classmethod
    def fail(
        cls,
        error_class: str,
        error_message: str = "worker job failed",
    ) -> "JobOutcome":
        return cls("fail", error_class=error_class, error_message=error_message)


@runtime_checkable
class JobHandler(Protocol):
    def __call__(
        self,
        context: JobContext,
        payload: Mapping[str, object],
    ) -> Awaitable[JobOutcome]: ...


class WorkerDispatcher:
    """Map stable job kinds to narrow async handlers without framework imports."""

    def __init__(self) -> None:
        self._handlers: dict[str, JobHandler] = {}

    def register(self, kind: str, handler: JobHandler) -> None:
        normalized_kind = _required_text(kind, "job kind")
        if normalized_kind in self._handlers:
            raise ValueError(f"job handler already registered: {normalized_kind}")
        if not callable(handler):
            raise TypeError("job handler must be callable")
        self._handlers[normalized_kind] = handler

    @property
    def registered_kinds(self) -> tuple[str, ...]:
        return tuple(sorted(self._handlers))

    async def dispatch(
        self,
        job: LeasedJob,
        *,
        stop_event: asyncio.Event,
    ) -> JobOutcome:
        handler = self._handlers.get(job.job_kind)
        if handler is None:
            return JobOutcome.fail(
                "UnknownJobKind",
                "worker job kind is not registered",
            )
        context = JobContext(
            job_id=job.id,
            user_uid=job.user_uid,
            account_id=job.account_id,
            provider_key=job.provider_key,
            queue_name=job.queue_name,
            worker_id=job.lease_owner,
            attempt_count=job.attempt_count,
            stop_event=stop_event,
        )
        payload = _freeze(job.payload)
        try:
            outcome = await handler(context, payload)
        except asyncio.CancelledError:
            raise
        except ProviderError as exc:
            if exc.retryable:
                return JobOutcome.retry(
                    exc.code.value,
                    exc.safe_detail,
                )
            return JobOutcome.fail(exc.code.value, exc.safe_detail)
        except Exception as exc:
            return JobOutcome.retry(
                type(exc).__name__,
                "worker handler raised an unexpected error",
            )
        if not isinstance(outcome, JobOutcome):
            return JobOutcome.fail(
                "InvalidJobOutcome",
                "worker handler returned an invalid outcome",
            )
        return outcome
