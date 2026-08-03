"""Production Worker dependency assembly and stable job-kind adapters."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from flymail.config import FlyMailSettings
from flymail.infrastructure.db.pool import DatabasePool
from flymail.infrastructure.object_store.store import ObjectStore
from flymail.infrastructure.security.credentials import CredentialCipher
from flymail.notifications.channels import ChannelRegistry
from flymail.notifications.image_publishers import ImagePublisherRegistry
from flymail.providers.registry import ProviderRegistry
from flymail.providers.runtime import ProductionProviderRuntime
from flymail.repositories.base import TenantContext
from flymail.workers.account_cleanup import AccountDataCleanupGateway
from flymail.workers.accounts import AccountCleanupHandler, AccountVerificationHandler
from flymail.workers.bulk_operations import BulkMarkReadHandler
from flymail.workers.cache_cleanup import CacheCleanupHandler
from flymail.workers.content_fetch import ContentFetchService, ContentJobPublisher
from flymail.workers.dispatcher import JobContext, JobOutcome, WorkerDispatcher
from flymail.workers.notifications import NotificationDeliveryHandler
from flymail.workers.operation_apply import OperationApplyHandler
from flymail.workers.sender import ReliableSender


_CONTENT_JOB_KINDS = frozenset(
    {
        "content.attachment",
        "content.body",
        "content.inline",
        "content.raw_eml",
    }
)
_SYNC_JOB_KINDS = frozenset(
    {
        "sync.incremental",
        "sync.initial",
        "sync.mailbox_refresh",
        "sync.reconcile",
    }
)


class ProviderWorkerRuntime(Protocol):
    async def verify(self, **kwargs) -> None: ...

    async def cleanup(self, **kwargs) -> None: ...

    def stream(self, locator, fetch_spec): ...

    async def observe(self, operation): ...

    async def apply(self, command): ...

    async def send(self, request): ...

    async def verify_sent(self, request): ...

    async def append_sent_copy(self, request): ...

    async def synchronize(
        self,
        context: JobContext,
        payload: Mapping[str, object],
        *,
        job_kind: str,
    ) -> JobOutcome: ...


class ContentJobHandler:
    """Route one stable content job kind to the matching content service method."""

    def __init__(self, service: ContentFetchService, job_kind: str) -> None:
        normalized = str(job_kind or "").strip()
        if normalized not in _CONTENT_JOB_KINDS:
            raise ValueError(f"unsupported content job kind: {normalized}")
        self.service = service
        self.job_kind = normalized

    async def __call__(
        self,
        context: JobContext,
        payload: Mapping[str, object],
    ) -> JobOutcome:
        if not context.user_uid:
            return JobOutcome.fail("InvalidContentScope", "content job requires a user")
        tenant = TenantContext(context.user_uid)
        if self.job_kind == "content.body":
            message_id = _required_payload_text(payload, "message_id")
            await self.service.fetch_body(tenant, message_id)
        elif self.job_kind == "content.inline":
            attachment_id = _required_payload_text(payload, "attachment_id")
            await self.service.fetch_inline(tenant, attachment_id)
        elif self.job_kind == "content.attachment":
            attachment_id = _required_payload_text(payload, "attachment_id")
            await self.service.fetch_attachment(
                tenant,
                attachment_id,
                supports_partial=True,
            )
        else:
            message_id = _required_payload_text(payload, "message_id")
            await self.service.fetch_raw_eml(tenant, message_id)
        return JobOutcome.success()


class SyncJobHandler:
    """Delegate one stable sync job kind to the provider runtime."""

    def __init__(self, runtime: ProviderWorkerRuntime, job_kind: str) -> None:
        normalized = str(job_kind or "").strip()
        if normalized not in _SYNC_JOB_KINDS:
            raise ValueError(f"unsupported sync job kind: {normalized}")
        self.runtime = runtime
        self.job_kind = normalized

    async def __call__(
        self,
        context: JobContext,
        payload: Mapping[str, object],
    ) -> JobOutcome:
        return await self.runtime.synchronize(
            context,
            payload,
            job_kind=self.job_kind,
        )


def _required_payload_text(payload: Mapping[str, object], key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ValueError(f"{key} is required")
    return value


def build_production_worker_dispatcher(
    pool: DatabasePool,
    settings: FlyMailSettings,
    *,
    provider_runtime: ProviderWorkerRuntime | None = None,
) -> WorkerDispatcher:
    """Construct the exact production handler graph for all durable job kinds."""

    if not isinstance(pool, DatabasePool):
        raise TypeError("pool must be DatabasePool")
    if not isinstance(settings, FlyMailSettings) or settings.role != "worker":
        raise TypeError("settings must be FlyMailSettings for worker role")

    registry = ProviderRegistry.default()
    store = ObjectStore(settings.object_dir, settings.object_tmp_dir)
    runtime = provider_runtime or ProductionProviderRuntime(
        pool,
        settings,
        registry=registry,
    )
    content = ContentFetchService(
        pool,
        store,
        runtime,
        ContentJobPublisher(pool),
        body_limit_bytes=8 * 1024 * 1024,
        attachment_limit_bytes=max(
            registry.get(key).capabilities().max_attachment_bytes
            for key in registry.keys()
        ),
        partial_chunk_bytes=1024 * 1024,
    )
    if isinstance(runtime, ProductionProviderRuntime):
        runtime.bind_content_service(content)
    sender = ReliableSender(pool, store, runtime, registry)
    cipher = CredentialCipher.from_master_secret(settings.session_secret)
    notification = NotificationDeliveryHandler(
        pool,
        store,
        cipher,
        ChannelRegistry.default(),
        ImagePublisherRegistry.default(),
    )

    handlers = {
        "account.cleanup": AccountCleanupHandler(
            pool,
            AccountDataCleanupGateway(pool, store),
        ),
        "account.verify": AccountVerificationHandler(
            pool,
            settings.session_secret,
            runtime,
            registry=registry,
        ),
        "cache.cleanup": CacheCleanupHandler(pool, store),
        "content.attachment": ContentJobHandler(content, "content.attachment"),
        "content.body": ContentJobHandler(content, "content.body"),
        "content.inline": ContentJobHandler(content, "content.inline"),
        "content.raw_eml": ContentJobHandler(content, "content.raw_eml"),
        "mail.operation.apply": OperationApplyHandler(pool, runtime, registry),
        "mail.operation.bulk_mark_read": BulkMarkReadHandler(pool, registry),
        "notification.deliver": notification.handle,
        "send.append_sent_copy": sender.append_sent_copy,
        "send.deliver": sender.handle,
        "send.verify": sender.verify,
        "sync.incremental": SyncJobHandler(runtime, "sync.incremental"),
        "sync.initial": SyncJobHandler(runtime, "sync.initial"),
        "sync.mailbox_refresh": SyncJobHandler(runtime, "sync.mailbox_refresh"),
        "sync.reconcile": SyncJobHandler(runtime, "sync.reconcile"),
    }

    from flymail.workers.main import build_worker_dispatcher

    return build_worker_dispatcher(handlers)


__all__ = [
    "ContentJobHandler",
    "ProviderWorkerRuntime",
    "SyncJobHandler",
    "build_production_worker_dispatcher",
]
